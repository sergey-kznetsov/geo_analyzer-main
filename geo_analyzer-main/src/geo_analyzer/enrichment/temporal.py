from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd


def _is_open_at_hour(opening_hours: str | None, hour: int) -> bool | None:
    if not opening_hours:
        return None

    text = str(opening_hours).strip().lower()
    if not text:
        return None
    if "24/7" in text or "круглосуточ" in text:
        return True

    match = re.search(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})", text)
    if not match:
        return None

    start_hour = int(match.group(1))
    end_hour = int(match.group(3))

    if start_hour <= end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def build_temporal_snapshot(pois: pd.DataFrame, hours: Iterable[int] = (8, 14, 20)) -> pd.DataFrame:
    explanation = (
        "Показывает, какая доля инфраструктуры реально работает в разное время суток. "
        "Это помогает понять, живёт ли локация только днём или остаётся активной вечером."
    )

    if pois is None or pois.empty:
        return pd.DataFrame(
            columns=[
                "Час",
                "Всего_объектов",
                "Открыто_объектов",
                "Доля_открытых_объектов_проц",
                "Объектов_без_графика",
                "Пояснение",
                "Шкала_оценки",
            ]
        )

    rows: list[dict[str, float | int | str]] = []
    total_objects = int(len(pois))
    opening_col = "Часы_работы" if "Часы_работы" in pois.columns else "opening_hours"

    for hour in hours:
        statuses = pois.get(opening_col, pd.Series([None] * len(pois))).apply(
            lambda value: _is_open_at_hour(value, int(hour))
        )
        open_objects = int((statuses == True).sum())
        unknown_objects = int(statuses.isna().sum())
        known_objects = total_objects - unknown_objects
        open_share = round((open_objects / known_objects * 100), 2) if known_objects > 0 else 0.0

        rows.append(
            {
                "Час": int(hour),
                "Всего_объектов": total_objects,
                "Открыто_объектов": open_objects,
                "Доля_открытых_объектов_проц": open_share,
                "Объектов_без_графика": unknown_objects,
                "Пояснение": explanation,
                "Шкала_оценки": "0–100, где 100 — почти вся инфраструктура работает в выбранный час",
            }
        )

    return pd.DataFrame(rows)