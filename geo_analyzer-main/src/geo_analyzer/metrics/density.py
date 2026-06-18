from __future__ import annotations

import re
from typing import Any

import pandas as pd


PRIMARY_CATEGORY_PRIORITY = [
    "торговые центры",
    "парки",
    "скверы",
    "театры",
    "музеи",
    "кинотеатры",
    "стадионы",
    "детские игровые залы",
    "детские развлекательные центры",
    "супермаркеты",
    "продуктовые магазины",
    "аптеки",
    "детские сады",
    "школы",
    "пункты выдачи интернет-заказов",
    "кафе",
    "рестораны",
    "кофейни",
    "пекарни",
    "фитнес-клубы",
]


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).replace("ё", "е").strip().lower()
    return re.sub(r"\s+", " ", text)


def _display_text(value: Any) -> str:
    text = str(value).replace("ё", "е").strip()
    return re.sub(r"\s+", " ", text)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    if isinstance(value, (list, tuple, set)):
        return [_display_text(item) for item in value if _display_text(item)]
    text = _display_text(value)
    if not text:
        return []
    if "," in text:
        return [_display_text(part) for part in text.split(",") if _display_text(part)]
    return [text]


def _join_examples(values: pd.Series, limit: int = 5) -> str:
    examples: list[str] = []
    for value in values.dropna().astype(str).tolist():
        text = value.strip()
        if text and text not in examples:
            examples.append(text)
        if len(examples) >= limit:
            break
    return "; ".join(examples)


def _join_unique(values: pd.Series, limit: int = 10) -> str:
    result: list[str] = []
    for value in values.dropna().tolist():
        for candidate in _as_list(value):
            if candidate and candidate not in result:
                result.append(candidate)
            if len(result) >= limit:
                break
        if len(result) >= limit:
            break
    return "; ".join(result)


def _safe_numeric_sum(values: pd.Series) -> float:
    return float(pd.to_numeric(values, errors="coerce").fillna(0).sum())


def _safe_numeric_mean(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return 0.0
    return float(numeric.mean())


def _ensure_columns(df: pd.DataFrame, columns: dict[str, Any]) -> pd.DataFrame:
    result = df.copy()
    for column, default_value in columns.items():
        if column not in result.columns:
            result[column] = default_value
    return result


def _priority_index(category: str) -> int:
    normalized = _normalize_text(category)
    for idx, marker in enumerate(PRIMARY_CATEGORY_PRIORITY):
        if marker in normalized:
            return idx
    return len(PRIMARY_CATEGORY_PRIORITY) + 100


def _pick_primary_category(row: pd.Series) -> str:
    candidates: list[str] = []
    for column in ["source_categories_2gis", "rubrics_2gis", "Категория_2GIS", "source_category_2gis", "Категория"]:
        if column not in row.index:
            continue
        for item in _as_list(row.get(column)):
            if item and item not in candidates:
                candidates.append(item)

    if not candidates:
        return "Прочее"

    candidates = sorted(candidates, key=lambda value: (_priority_index(value), _normalize_text(value)))
    return candidates[0] or "Прочее"


def _prepare_functional_category(data: pd.DataFrame) -> pd.Series:
    if "functional_category" in data.columns:
        functional = data["functional_category"].copy()
    elif "Сценарная_группа" in data.columns:
        functional = data["Сценарная_группа"].copy()
    else:
        functional = pd.Series(["Прочее"] * len(data), index=data.index)
    functional = functional.fillna("Прочее").astype(str).str.strip().replace("", "Прочее")
    return functional


def build_category_summary(pois: pd.DataFrame) -> pd.DataFrame:
    """Формирует стабильную сводку по основным категориям 2GIS.

    Если один объект пришёл из нескольких рубрик, категория выбирается
    детерминированно по приоритету и алфавиту. Это убирает скачки топа
    категорий между запусками на одной и той же точке.
    """
    output_columns = [
        "Категория",
        "Категория_2GIS",
        "functional_category",
        "Количество",
        "Доля_проц",
        "Суммарный_вес_критичности",
        "Средний_вес_критичности",
        "Статус_классификации",
        "Исходные_категории_2GIS",
        "Примеры_объектов",
        "Примеры_адресов",
        "Пояснение",
    ]

    if pois is None or pois.empty:
        return pd.DataFrame(columns=output_columns)

    data = pois.copy()
    data = _ensure_columns(
        data,
        {
            "Название": pd.NA,
            "Адрес": pd.NA,
            "criticality_score": 0,
            "classification_status": "not_mapped",
            "source_category_2gis": pd.NA,
            "source_categories_2gis": pd.NA,
            "rubrics_2gis": pd.NA,
        },
    )

    data["Категория_2GIS"] = data.apply(_pick_primary_category, axis=1)
    data["Категория"] = data["Категория_2GIS"]
    data["functional_category"] = _prepare_functional_category(data)
    data["criticality_score"] = pd.to_numeric(data["criticality_score"], errors="coerce").fillna(0)

    grouped = (
        data.groupby(["Категория_2GIS", "functional_category"], dropna=False)
        .agg(
            Количество=("Категория_2GIS", "size"),
            Суммарный_вес_критичности=("criticality_score", _safe_numeric_sum),
            Средний_вес_критичности=("criticality_score", _safe_numeric_mean),
            Статус_классификации=("classification_status", _join_unique),
            Исходные_категории_2GIS=("source_category_2gis", _join_unique),
            Примеры_объектов=("Название", _join_examples),
            Примеры_адресов=("Адрес", _join_examples),
        )
        .reset_index()
    )

    grouped["Категория"] = grouped["Категория_2GIS"]
    total = int(grouped["Количество"].sum()) if not grouped.empty else 0
    grouped["Доля_проц"] = (grouped["Количество"] / total * 100).round(2) if total else 0.0
    grouped["Суммарный_вес_критичности"] = pd.to_numeric(grouped["Суммарный_вес_критичности"], errors="coerce").fillna(0).round(2)
    grouped["Средний_вес_критичности"] = pd.to_numeric(grouped["Средний_вес_критичности"], errors="coerce").fillna(0).round(2)
    grouped["Пояснение"] = (
        "Сводка построена по стабильной основной категории 2GIS. "
        "Если объект пришёл из нескольких рубрик, категория выбирается детерминированно."
    )

    grouped = grouped.sort_values(
        ["Количество", "Суммарный_вес_критичности", "Категория_2GIS"],
        ascending=[False, False, True],
    ).reset_index(drop=True)

    return grouped[output_columns]
