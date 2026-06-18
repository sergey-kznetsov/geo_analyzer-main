"""Отдельный пайплайн конкурентного анализа."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from geo_analyzer.competition import CompetitionResult, analyze_competition
from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.models import LocationInput, ResolvedLocation
from geo_analyzer.core.settings import get_settings

logger = get_logger("geo_analyzer.pipeline.competition")

ProgressCallback = Callable[..., None] | None


def _notify(progress_callback: ProgressCallback, step: int, total: int, message: str) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(step=step, total=total, message=message)
    except TypeError:
        progress_callback(step, total, message)


def _resolve_location(location_input: LocationInput) -> ResolvedLocation:
    if location_input.latitude is not None and location_input.longitude is not None:
        return ResolvedLocation(
            latitude=float(location_input.latitude),
            longitude=float(location_input.longitude),
            resolved_address=f"{location_input.latitude}, {location_input.longitude}",
            source_label="coordinates",
        )

    if location_input.address:
        from geo_analyzer.ingestion.dgis.geocoder import DGISGeocoder

        resolved = DGISGeocoder().geocode(location_input.address)
        return ResolvedLocation(
            latitude=resolved.latitude,
            longitude=resolved.longitude,
            resolved_address=resolved.resolved_address,
            source_label="address",
        )

    raise ValueError("Не передан адрес или координаты.")


def _export(result: CompetitionResult, location: ResolvedLocation) -> Path:
    settings = get_settings()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = settings.output_dir / "competition"
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / f"competition_{stamp}.xlsx"

    sheets: dict[str, pd.DataFrame] = {
        "Сводка конкурентов": result.summary,
        "Детализация конкурентов": result.competitors,
        "Застройщики": result.developers,
    }

    try:
        with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as writer:
            for sheet_name, df in sheets.items():
                export_df = df if isinstance(df, pd.DataFrame) and not df.empty else pd.DataFrame({"Статус": [result.text_summary]})
                export_df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    except Exception as exc:
        logger.warning("Не удалось сохранить Excel конкурентного анализа: %s", exc)

    json_path = out_dir / f"competition_{stamp}.json"
    payload: dict[str, Any] = {
        "location": {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "address": location.resolved_address,
        },
        "text_summary": result.text_summary,
        "gui_label": result.gui_label,
        "benchmark_context": result.benchmark_context,
        "summary": result.summary.to_dict("records"),
        "competitors": result.competitors.to_dict("records"),
        "developers": result.developers.to_dict("records"),
    }

    try:
        json_path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Не удалось сохранить JSON конкурентного анализа: %s", exc)

    return xlsx_path


def run_competition_analysis(
    location_input: LocationInput,
    *,
    radius_m: int | None = None,
    progress_callback: ProgressCallback = None,
) -> CompetitionResult:
    settings = get_settings()
    started = time.perf_counter()

    _notify(progress_callback, 1, 5, "Геокодирование через 2GIS")
    location = _resolve_location(location_input)
    print(f"[1/5] Конкурентный анализ: {location.resolved_address}", flush=True)

    _notify(progress_callback, 2, 5, "Определение region_id и рубрик 2GIS")
    _notify(progress_callback, 3, 5, "Загрузка новостроек и строящихся объектов через 2GIS API")
    result = analyze_competition(
        latitude=location.latitude,
        longitude=location.longitude,
        radius_m=int(radius_m or settings.poi_radius_m),
    )

    _notify(progress_callback, 4, 5, "Экспорт competition.xlsx")
    out_path = _export(result, location)

    _notify(progress_callback, 5, 5, "Конкурентный анализ готов")
    print(f"[OK] Конкурентный анализ сохранён: {out_path} ({time.perf_counter() - started:.2f} сек)", flush=True)

    return result
