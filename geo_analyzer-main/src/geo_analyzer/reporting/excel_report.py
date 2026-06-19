from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from geo_analyzer.core.utils import normalize_quality_scores, safe_value


TECHNICAL_COLUMNS = {
    "raw_2gis",
    "geometry",
    "dgis_id",
    "fid",
    "rubric_id",
    "rubrics_2gis",
    "source_categories_2gis",
    "source_category_2gis",
    "category_groups_2gis",
    "classification_rule_id",
    "classification_status",
    "validation_status",
}


def _df(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list):
        return pd.DataFrame(value)
    if isinstance(value, dict):
        return pd.DataFrame([value])
    return pd.DataFrame()


def _safe_df(value: Any) -> pd.DataFrame:
    data = _df(value)
    if data.empty:
        return data
    data = data.drop(columns=[c for c in data.columns if str(c) in TECHNICAL_COLUMNS], errors="ignore")
    for column in data.columns:
        data[column] = data[column].map(safe_value)
    return data


def _summary_df(result: dict[str, Any]) -> pd.DataFrame:
    meta = result.get("meta", {}) or {}
    drive = result.get("drive_metrics", {}) or {}
    quality = normalize_quality_scores(result.get("quality_scores", pd.DataFrame()))
    category = _df(result.get("category_summary"))
    anti = _df(result.get("anti_driver_summary"))

    rows: list[dict[str, Any]] = [
        {"Раздел": "Общий вывод", "Показатель": "Текстовое саммари", "Значение": result.get("text_summary"), "Комментарий": "Краткая интерпретация результата."},
        {"Раздел": "Вводные", "Показатель": "Адрес / метка", "Значение": meta.get("resolved_address"), "Комментарий": "Точка анализа."},
        {"Раздел": "Вводные", "Показатель": "Координаты", "Значение": f"{meta.get('latitude')}, {meta.get('longitude')}", "Комментарий": "Широта и долгота."},
        {"Раздел": "Вводные", "Показатель": "Город / регион", "Значение": meta.get("city") or meta.get("region_name"), "Комментарий": "Контекст анализа."},
        {"Раздел": "Вводные", "Показатель": "Провайдер", "Значение": meta.get("provider", "2GIS"), "Комментарий": "Источник данных."},
    ]

    if not quality.empty and "Оценка_из_10" in quality.columns:
        scores = pd.to_numeric(quality["Оценка_из_10"], errors="coerce").dropna()
        if not scores.empty:
            rows.append({"Раздел": "Качество среды", "Показатель": "Средняя оценка", "Значение": round(float(scores.mean()), 2), "Комментарий": "Среднее по метрикам 0-10."})
            best = quality.loc[pd.to_numeric(quality["Оценка_из_10"], errors="coerce").idxmax()]
            weak = quality.loc[pd.to_numeric(quality["Оценка_из_10"], errors="coerce").idxmin()]
            rows.append({"Раздел": "Качество среды", "Показатель": "Сильнейшая метрика", "Значение": f"{best.get('Метрика')} — {best.get('Оценка_из_10')}", "Комментарий": "Сильная сторона."})
            rows.append({"Раздел": "Качество среды", "Показатель": "Слабейшая метрика", "Значение": f"{weak.get('Метрика')} — {weak.get('Оценка_из_10')}", "Комментарий": "Зона внимания."})

    if not category.empty and "Количество" in category.columns:
        rows.append({"Раздел": "Инфраструктура", "Показатель": "Всего POI", "Значение": int(pd.to_numeric(category["Количество"], errors="coerce").fillna(0).sum()), "Комментарий": "После загрузки и классификации."})

    if not anti.empty:
        count = pd.to_numeric(anti.get("Количество", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
        rows.append({"Раздел": "Антидрайверы", "Показатель": "Всего антидрайверов", "Значение": int(count), "Комментарий": "Факторы, снижающие качество среды."})

    rows.extend([
        {"Раздел": "Авто-доступность", "Показатель": "Центр для оценки", "Значение": drive.get("center_name") or "нет данных", "Комментарий": "Центр для маршрута."},
        {"Раздел": "Авто-доступность", "Показатель": "Время до центра, мин", "Значение": drive.get("drive_time_min") or drive.get("time_min"), "Комментарий": "Автомобильная доступность."},
        {"Раздел": "Авто-доступность", "Показатель": "Расстояние до центра, км", "Значение": drive.get("drive_distance_km") or drive.get("distance_km"), "Комментарий": "Автомобильное расстояние."},
    ])
    return pd.DataFrame(rows)


def _style(output_path: Path) -> None:
    wb = load_workbook(output_path)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    align = Alignment(wrap_text=True, vertical="top")
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = align
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = align
        for column_cells in ws.columns:
            width = max((len(str(cell.value)) for cell in column_cells if cell.value is not None), default=8)
            ws.column_dimensions[get_column_letter(column_cells[0].column)].width = min(max(width + 2, 12), 60)
        ws.auto_filter.ref = ws.dimensions
    wb.save(output_path)


def export_report_to_excel(result: dict[str, Any], visuals: dict[str, Any], gamma_prompt: str, output_path: Path) -> Path:
    del visuals, gamma_prompt
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheets = {
        "Саммари": _summary_df(result),
        "Качество среды": normalize_quality_scores(result.get("quality_scores", pd.DataFrame())),
        "Доступность": _safe_df(result.get("accessibility_snapshot")),
        "POI по изохронам": _safe_df(result.get("poi_details_by_iso")),
        "Точки притяжения": _safe_df(result.get("attraction_points")),
        "Антидрайверы": _safe_df(result.get("anti_driver_summary")),
        "Бенчмарки": _safe_df(result.get("benchmark_summary")),
        "Сетевые метрики": _safe_df(result.get("network_metrics")),
    }
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            _safe_df(df).to_excel(writer, sheet_name=name[:31], index=False)
    _style(output_path)
    return output_path
