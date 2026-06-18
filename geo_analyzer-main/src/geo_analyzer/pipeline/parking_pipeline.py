from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.models import LocationInput
from geo_analyzer.core.settings import get_settings
from geo_analyzer.core.utils import safe_value
from geo_analyzer.enrichment.validators import validate_location_input
from geo_analyzer.geometry.isochrones import build_isochrones
from geo_analyzer.ingestion.dgis.places_loader import load_places_near_point
from geo_analyzer.metrics.parking_supply import ParkingSupplyResult, calculate_parking_supply
from geo_analyzer.pipeline.analysis_pipeline import _resolve_location
from geo_analyzer.pipeline.context_builder import build_analysis_context
from geo_analyzer.reporting.summary_writer import export_text_summary

try:
    from geo_analyzer.enrichment.categories import classify_pois
except ImportError:
    from geo_analyzer.enrichment.poi_classifier import classify_pois


logger = get_logger("geo_analyzer.pipeline.parking")
ProgressCallback = Callable[..., None] | None


def _notify_progress(progress_callback: ProgressCallback, step: int, total: int, message: str) -> None:
    if progress_callback is None:
        return
    payload = {"step": step, "total": total, "percent": round(step / total * 100), "message": message}
    variants = [
        lambda: progress_callback(step, total, message),
        lambda: progress_callback(step, message),
        lambda: progress_callback(message),
        lambda: progress_callback(payload),
    ]
    for variant in variants:
        try:
            variant()
            return
        except TypeError:
            continue
        except Exception:
            return


def _stage(name: str, started_at: float) -> None:
    elapsed = time.perf_counter() - started_at
    print(f"[OK] {name}: {elapsed:.2f} сек", flush=True)


def run_parking_analysis(location_input: LocationInput, *, progress_callback: ProgressCallback = None) -> dict[str, Any]:
    """Автономный расчёт парковочного потенциала.

    Этот pipeline не участвует в основном анализе и сравнении локаций.
    Он сам геокодирует адрес, загружает POI, строит изохроны, считает парковки
    и сохраняет отдельный Excel/summary/meta в папке результата.
    """
    total_started = time.perf_counter()
    total_steps = 8

    validate_location_input(location_input.address, location_input.latitude, location_input.longitude)
    settings = get_settings()

    _notify_progress(progress_callback, 1, total_steps, "Геокодирование через 2GIS")
    print("[1/8] Геокодирование через 2GIS", flush=True)
    started = time.perf_counter()
    location = _resolve_location(location_input)
    _stage("Геокодирование", started)

    _notify_progress(progress_callback, 2, total_steps, "Построение контекста парковочного отчёта")
    print("[2/8] Построение контекста парковочного отчёта", flush=True)
    started = time.perf_counter()
    context = build_analysis_context(location)
    _stage("Построение контекста", started)

    _notify_progress(progress_callback, 3, total_steps, "Загрузка POI через 2GIS Places")
    print("[3/8] Загрузка POI через 2GIS Places", flush=True)
    started = time.perf_counter()
    pois_raw = load_places_near_point(location.latitude, location.longitude, radius_m=settings.poi_radius_m)
    _stage(f"Загрузка POI ({len(pois_raw)})", started)

    _notify_progress(progress_callback, 4, total_steps, "Классификация POI")
    print("[4/8] Классификация POI", flush=True)
    started = time.perf_counter()
    pois = classify_pois(pois_raw)
    _stage("Классификация POI", started)

    _notify_progress(progress_callback, 5, total_steps, "Построение изохрон через 2GIS")
    print("[5/8] Построение изохрон через 2GIS", flush=True)
    started = time.perf_counter()
    isochrones = build_isochrones(
        location.latitude,
        location.longitude,
        settings.graph_dist_m,
        settings.isochrone_minutes,
        settings.walk_speed_kph,
    )
    _stage(f"Построение изохрон ({len(isochrones)})", started)

    _notify_progress(progress_callback, 6, total_steps, "Расчёт парковочного потенциала")
    print("[6/8] Расчёт парковочного потенциала", flush=True)
    started = time.perf_counter()
    parking_supply = calculate_parking_supply(
        pois=pois,
        isochrones=isochrones,
        latitude=location.latitude,
        longitude=location.longitude,
        radius_m=settings.poi_radius_m,
    )
    _stage("Расчёт парковочного потенциала", started)

    report_path = context.result_dir / "parking_potential.xlsx"
    summary_path = context.result_dir / "parking_summary.txt"
    meta_path = context.result_dir / "parking_meta.json"

    text_summary = _build_parking_text_summary(parking_supply)
    meta = {
        "source_label": location.source_label,
        "resolved_address": location.resolved_address or location.source_label,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "poi_radius_m": settings.poi_radius_m,
        "graph_dist_m": settings.graph_dist_m,
        "isochrones_minutes": list(settings.isochrone_minutes),
        "walk_speed_kph": settings.walk_speed_kph,
        "provider": "2GIS",
        "result_type": "parking_potential",
        "result_dir": str(context.result_dir),
        "report_path": str(report_path),
        "summary_path": str(summary_path),
        "parking_summary": parking_supply.text_summary,
        "parking_gui_label": parking_supply.gui_label,
    }

    result = {
        "context": context,
        "result_dir": context.result_dir,
        "report_path": report_path,
        "summary_path": summary_path,
        "meta": meta,
        "pois_raw": pois_raw.copy() if hasattr(pois_raw, "copy") else pois_raw,
        "pois": pois,
        "isochrones": isochrones,
        "parking_supply_summary": parking_supply.summary,
        "parking_details": parking_supply.parking_details,
        "residential_details": parking_supply.residential_details,
        "parking_text_summary": parking_supply.text_summary,
        "parking_gui_label": parking_supply.gui_label,
        "text_summary": text_summary,
    }

    _notify_progress(progress_callback, 7, total_steps, "Экспорт парковочного отчёта")
    print("[7/8] Экспорт парковочного отчёта", flush=True)
    started = time.perf_counter()
    export_parking_report(result, report_path)
    export_text_summary(text_summary, summary_path)
    _stage("Экспорт парковочного отчёта", started)

    _notify_progress(progress_callback, 8, total_steps, "Сохранение meta.json")
    print("[8/8] Сохранение meta.json", flush=True)
    started = time.perf_counter()
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _stage("Сохранение meta.json", started)

    _notify_progress(progress_callback, total_steps, total_steps, "Парковочный потенциал готов")
    print(f"[DONE] Парковочный потенциал рассчитан за {time.perf_counter() - total_started:.2f} сек", flush=True)
    logger.info("Парковочный потенциал готов: %s", context.result_dir)
    return result


def export_parking_report(result: dict[str, Any], output_path: Path) -> None:
    meta = result.get("meta", {}) or {}
    summary_df = _summary_sheet(meta, result)
    parking_summary = _excel_safe_df(result.get("parking_supply_summary"))
    parking_details = _excel_safe_df(result.get("parking_details"))
    residential_details = _excel_safe_df(result.get("residential_details"))

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Сводка")
        parking_summary.to_excel(writer, index=False, sheet_name="Парковочный потенциал")
        parking_details.to_excel(writer, index=False, sheet_name="Детализация парковок")
        residential_details.to_excel(writer, index=False, sheet_name="Жилые дома")


def _summary_sheet(meta: dict[str, Any], result: dict[str, Any]) -> pd.DataFrame:
    rows = [
        {"Показатель": "Адрес", "Значение": meta.get("resolved_address")},
        {"Показатель": "Координаты", "Значение": f"{meta.get('latitude')}, {meta.get('longitude')}"},
        {"Показатель": "Радиус загрузки POI, м", "Значение": meta.get("poi_radius_m")},
        {"Показатель": "Изохроны, мин", "Значение": ", ".join(map(str, meta.get("isochrones_minutes", [])))},
        {"Показатель": "Краткий вывод", "Значение": result.get("parking_text_summary")},
        {"Показатель": "GUI-вывод", "Значение": result.get("parking_gui_label")},
    ]
    return pd.DataFrame(rows)


def _excel_safe_df(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        data = value.copy()
    elif isinstance(value, list):
        data = pd.DataFrame(value)
    else:
        return pd.DataFrame()
    if data.empty:
        return data
    for column in data.columns:
        data[column] = data[column].map(safe_value)
    return data


def _first_row_value(row: pd.Series, *columns: str, default: Any = "—") -> Any:
    for column in columns:
        value = row.get(column)
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        if value is not None:
            return value
    return default


def _build_parking_text_summary(parking_supply: ParkingSupplyResult) -> str:
    lines = ["Парковочный потенциал рассчитан автономно, отдельно от основного анализа локации."]
    if parking_supply.text_summary:
        lines.append(parking_supply.text_summary)
    if parking_supply.summary is not None and not parking_supply.summary.empty:
        lines.append("")
        lines.append("Сводка по зонам:")
        for _, row in parking_supply.summary.iterrows():
            zone = row.get("Зона", "—")
            score = _first_row_value(
                row,
                "Парковочный_потенциал_из_10",
                "Оценка_из_10",
                "Парковочный_коэффициент",
                "Потенциал_из_10",
            )
            cls = _first_row_value(row, "Класс_парковочного_потенциала", "Класс_обеспеченности")
            houses = _first_row_value(row, "Жилых_домов")
            apartments = _first_row_value(row, "Квартир_в_зоне")
            spaces = _first_row_value(row, "Парковочных_мест")
            lines.append(f"{zone}: {score} из 10, {cls}; домов: {houses}, квартир: {apartments}, мест: {spaces}")
    return "\n".join(lines)
