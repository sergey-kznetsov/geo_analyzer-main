from __future__ import annotations

import re
from typing import Any

import geopandas as gpd
import pandas as pd

from geo_analyzer.core.settings import get_settings


def _normalize(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, float) and pd.isna(value):
        return ""

    text = str(value).replace("ё", "е").strip().lower()
    return re.sub(r"\s+", " ", text)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, float) and pd.isna(value):
        return []

    if isinstance(value, (list, tuple, set)):
        return [_normalize(item) for item in value if _normalize(item)]

    text = str(value).strip()
    if not text:
        return []

    if "," in text:
        return [_normalize(part) for part in text.split(",") if _normalize(part)]

    return [_normalize(text)]


def _row_text(row: pd.Series) -> str:
    parts: list[str] = []

    for column in [
        "Название",
        "Адрес",
        "Категория",
        "Категория_2GIS",
        "source_category_2gis",
        "source_categories_2gis",
        "rubrics_2gis",
        "category_groups_2gis",
        "branch_type_2gis",
        "rubric_id",
    ]:
        if column not in row.index:
            continue

        value = row.get(column)

        if isinstance(value, (list, tuple, set)):
            parts.extend(_as_list(value))
        else:
            text = _normalize(value)
            if text:
                parts.append(text)

    return " ".join(parts)


def _minutes(row: pd.Series) -> int | None:
    for column in ["Минут_пешком", "travel_time_min", "До_минут"]:
        if column not in row.index:
            continue

        value = row.get(column)

        try:
            if value is not None and not pd.isna(value):
                return int(float(value))
        except (TypeError, ValueError):
            continue

    return None


def _matches_minutes(row: pd.Series, rule: dict[str, Any]) -> bool:
    minutes = _minutes(row)

    if minutes is None:
        return True

    min_minutes = rule.get("min_minutes")
    max_minutes = rule.get("max_minutes")

    if min_minutes is not None and minutes < int(min_minutes):
        return False

    if max_minutes is not None and minutes > int(max_minutes):
        return False

    return True


def _contains_any(text: str, keywords: list[str]) -> bool:
    normalized_keywords = [_normalize(keyword) for keyword in keywords if _normalize(keyword)]
    return any(keyword in text for keyword in normalized_keywords)


def _is_vending_exclusion(row: pd.Series, config: dict[str, Any]) -> bool:
    exclusion = config.get("vending_exclusion", {})
    if not isinstance(exclusion, dict):
        return False

    name_keywords = exclusion.get("name_keywords", []) or []
    category_keywords = exclusion.get("category_keywords", []) or []

    name = _normalize(row.get("Название"))
    category_text = " ".join(
        [
            _normalize(row.get("Категория")),
            _normalize(row.get("Категория_2GIS")),
            _normalize(row.get("source_category_2gis")),
            _normalize(row.get("rubrics_2gis")),
        ]
    )

    return _contains_any(name, name_keywords) or _contains_any(category_text, category_keywords)


def classify_pois(pois: pd.DataFrame | None) -> pd.DataFrame:
    """Классифицирует POI по правилам из config.yaml.

    На выходе сохраняются исходные 2GIS-категории и добавляются продуктовые поля:
    category, functional_category, criticality_score, classification_rule_id,
    classification_status.
    """
    settings = get_settings()
    config = settings.poi_classification

    if pois is None:
        return pd.DataFrame()

    data = pois.copy()

    if data.empty:
        return data

    default_category = str(config.get("default_category", "Прочее"))
    default_scenario_group = str(config.get("default_scenario_group", "Фоновая среда"))
    default_functional_category = str(config.get("default_functional_category", "Прочее"))
    default_criticality_score = int(config.get("default_criticality_score", 0))

    if "Категория_2GIS" not in data.columns:
        data["Категория_2GIS"] = data.get("Категория", "Прочее")

    data["source_category_2gis"] = data["Категория_2GIS"]
    data["category"] = default_category
    data["Категория"] = data["Категория_2GIS"]
    data["scenario_group"] = default_scenario_group
    data["functional_category"] = default_functional_category
    data["criticality_score"] = default_criticality_score
    data["classification_rule_id"] = pd.NA
    data["classification_status"] = "not_mapped"
    data["validation_status"] = "ok"

    rules = config.get("rules", [])
    if not isinstance(rules, list):
        rules = []

    for idx, row in data.iterrows():
        if _is_vending_exclusion(row, config):
            exclusion = config.get("vending_exclusion", {})
            data.at[idx, "category"] = exclusion.get("target_category", default_category)
            data.at[idx, "scenario_group"] = exclusion.get("target_scenario_group", default_scenario_group)
            data.at[idx, "functional_category"] = exclusion.get("target_functional_category", default_functional_category)
            data.at[idx, "criticality_score"] = int(exclusion.get("target_criticality_score", default_criticality_score))
            data.at[idx, "classification_rule_id"] = "vending_exclusion"
            data.at[idx, "classification_status"] = "excluded"
            continue

        text = _row_text(row)

        for rule in rules:
            if not isinstance(rule, dict):
                continue

            source_categories = rule.get("source_categories", []) or []
            if not source_categories:
                continue

            if not _matches_minutes(row, rule):
                continue

            if not _contains_any(text, source_categories):
                continue

            data.at[idx, "category"] = str(rule.get("category", default_category))
            data.at[idx, "scenario_group"] = str(rule.get("scenario_group", default_scenario_group))
            data.at[idx, "functional_category"] = str(rule.get("functional_category", default_functional_category))
            data.at[idx, "criticality_score"] = int(rule.get("criticality_score", default_criticality_score))
            data.at[idx, "classification_rule_id"] = str(rule.get("rule_id", "rule"))
            data.at[idx, "classification_status"] = "mapped"
            break

    if isinstance(pois, gpd.GeoDataFrame):
        return gpd.GeoDataFrame(data, geometry="geometry", crs=pois.crs)

    return data