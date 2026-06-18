from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s\-_а-яё]", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[\s\-]+", "_", value)
    return value[:80].strip("_") or "result"


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_float(value: Any, default: float | None = None) -> float | None:
    """Безопасное преобразование значения в float."""
    try:
        if value is None:
            return default
        if isinstance(value, float) and pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any) -> int | None:
    """Безопасное преобразование значения в int."""
    if value is None:
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        match = re.search(r"(\d+)", str(value))
        return int(match.group(1)) if match else None


def to_numeric(series: pd.Series | Any, default: float = 0.0) -> pd.Series:
    """Преобразует Series в числовой формат с заполнением по умолчанию."""
    if isinstance(series, pd.Series):
        return pd.to_numeric(series, errors="coerce").fillna(default)
    return pd.Series([series]).pipe(pd.to_numeric, errors="coerce").fillna(default)


def normalize_quality_scores(df: pd.DataFrame | None) -> pd.DataFrame:
    """Нормализует качество оценок в стандартный формат (0-10)."""
    columns = ["Метрика", "Оценка_из_10", "Пояснение", "Шкала_оценки"]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)

    result = df.copy()
    if "Оценка_из_10" not in result.columns and "Оценка_из_100" in result.columns:
        result["Оценка_из_10"] = to_numeric(result["Оценка_из_100"]) / 10
    if "Оценка_из_10" not in result.columns:
        result["Оценка_из_10"] = 0

    result["Оценка_из_10"] = to_numeric(result["Оценка_из_10"]).clip(0, 10).round(2)

    for column in columns:
        if column not in result.columns:
            result[column] = pd.NA

    result["Шкала_оценки"] = "0-10"
    return result[columns]


def first_column(df: Any, candidates: list[str]) -> str | None:
    """Найти первую существующую колонку из списка кандидатов."""
    for candidate in candidates:
        if hasattr(df, "columns") and candidate in df.columns:
            return candidate
    return None


def clean_category_label(value: Any) -> str:
    """Очищает метку категории от лишних символов."""
    if value is None:
        return "Без категории"
    text = str(value).strip().replace("[", "").replace("]", "").replace("'", "").replace('"', "")
    if not text:
        return "Без категории"
    return text[:80]


def safe_value(value: Any) -> Any:
    """Безопасное преобразование значения для Excel."""
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str)
    try:
        if pd.isna(value):
            return pd.NA
    except (TypeError, ValueError):
        pass
    return value
