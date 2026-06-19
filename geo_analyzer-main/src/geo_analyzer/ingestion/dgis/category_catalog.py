from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings


logger = get_logger("geo_analyzer.dgis.category_catalog")

CATEGORY_CATALOG_VERSION = "fixed_configured_rubrics_v1"


@dataclass(frozen=True, slots=True)
class RegionRef:
    id: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class RubricRef:
    id: str
    name: str
    title: str = ""
    caption: str = ""
    alias: str = ""
    keyword: str = ""
    parent_id: str = ""
    type: str = "rubric"
    region_id: str = ""

    @property
    def display_name(self) -> str:
        return self.title or self.caption or self.name or self.keyword or self.alias or self.id


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "да", "on"}


def refresh_dgis_catalog_requested() -> bool:
    env_value = os.getenv("GEO_ANALYZER_REFRESH_DGIS_CATALOG")
    if env_value is not None:
        return _truthy(env_value)
    return False


def normalize_category_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("ё", "е").strip().lower()
    return re.sub(r"\s+", " ", text)


def get_region_for_point(latitude: float | None, longitude: float | None) -> RegionRef:
    del latitude, longitude
    settings = get_settings()
    dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
    return RegionRef(
        id=str(dgis_config.get("region_id") or settings.dgis_region_id or "").strip(),
        name=str(dgis_config.get("region_name") or "config_region").strip(),
    )


def flatten_category_items(items: list[dict[str, Any]], *, region_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any], parent_id: str = "") -> None:
        rubric_id = str(item.get("id", "") or "").strip()
        if not rubric_id or rubric_id in seen:
            return
        seen.add(rubric_id)
        result.append(
            {
                "id": rubric_id,
                "type": str(item.get("type") or "rubric"),
                "region_id": str(item.get("region_id") or region_id or ""),
                "parent_id": str(item.get("parent_id") or parent_id or ""),
                "name": str(item.get("name") or ""),
                "title": str(item.get("title") or ""),
                "caption": str(item.get("caption") or ""),
                "keyword": str(item.get("keyword") or ""),
                "alias": str(item.get("alias") or ""),
                "seo_name": str(item.get("seo_name") or ""),
            }
        )
        children = item.get("rubrics") or []
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    add(child, parent_id=rubric_id)

    for item in items:
        if isinstance(item, dict):
            add(item)

    return result


def load_or_fetch_category_catalog(*, region_id: str, locale: str = "ru_RU", refresh: bool = False) -> dict[str, Any]:
    del locale, refresh
    return {
        "metadata": {
            "source": "config.yaml fixed rubric_id",
            "endpoint": None,
            "region_id": str(region_id),
            "version": CATEGORY_CATALOG_VERSION,
        },
        "items": [],
    }


def _rubric_from_row(row: dict[str, Any]) -> RubricRef:
    return RubricRef(
        id=str(row.get("id", "") or "").strip(),
        name=str(row.get("name", "") or "").strip(),
        title=str(row.get("title", "") or "").strip(),
        caption=str(row.get("caption", "") or "").strip(),
        alias=str(row.get("alias", "") or "").strip(),
        keyword=str(row.get("keyword", "") or "").strip(),
        parent_id=str(row.get("parent_id", "") or "").strip(),
        type=str(row.get("type", "") or "rubric").strip(),
        region_id=str(row.get("region_id", "") or "").strip(),
    )


def build_catalog_indexes(catalog: dict[str, Any]) -> tuple[dict[str, RubricRef], dict[str, list[RubricRef]]]:
    by_id: dict[str, RubricRef] = {}
    by_name: dict[str, list[RubricRef]] = {}
    items = catalog.get("items", []) if isinstance(catalog, dict) else []
    if not isinstance(items, list):
        items = []

    for row in items:
        if not isinstance(row, dict):
            continue
        rubric = _rubric_from_row(row)
        if not rubric.id:
            continue
        by_id[rubric.id] = rubric
        for value in [rubric.name, rubric.title, rubric.caption, rubric.keyword, rubric.alias]:
            key = normalize_category_name(value)
            if key:
                by_name.setdefault(key, []).append(rubric)

    return by_id, by_name


def resolve_rubric_from_catalog(*, name: str, catalog: dict[str, Any], fallback_id: str | None = None) -> RubricRef | None:
    del catalog
    rid = str(fallback_id or "").strip()
    title = str(name or rid).strip()
    if not rid:
        return None
    return RubricRef(id=rid, name=title, title=title)


def resolve_configured_place_rubrics(
    entries: list[dict[str, Any]],
    *,
    latitude: float,
    longitude: float,
) -> tuple[list[dict[str, Any]], list[str], RegionRef, dict[str, Any]]:
    region = get_region_for_point(latitude, longitude)
    catalog = load_or_fetch_category_catalog(region_id=region.id)
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    used_ids: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        category = str(entry.get("category") or "").strip()
        rubric_name = str(entry.get("rubric_name") or category or "").strip()
        rubric_id = str(entry.get("rubric_id") or "").strip()

        if not category:
            continue

        if not rubric_id:
            missing.append(rubric_name or category)
            continue

        if rubric_id in used_ids:
            continue

        used_ids.add(rubric_id)
        resolved.append(
            {
                "category": category,
                "rubric_name": rubric_name or category,
                "rubric_id": rubric_id,
                "region_id": region.id,
                "region_name": region.name,
                "official_name": rubric_name or category,
                "official_title": rubric_name or category,
                "official_caption": rubric_name or category,
                "official_alias": "",
                "official_keyword": "",
                "official_parent_id": "",
                "official_type": "rubric",
                "official_region_id": region.id,
            }
        )

    logger.info(
        "Рубрики 2GIS взяты из config.yaml: region_id=%s configured=%d resolved=%d missing=%d",
        region.id,
        len(entries),
        len(resolved),
        len(missing),
    )
    return resolved, missing, region, catalog


def official_category_name_for_rubric_id(rubric_id: Any, catalog: dict[str, Any]) -> str | None:
    rid = str(rubric_id or "").strip()
    if not rid:
        return None
    by_id, _ = build_catalog_indexes(catalog)
    rubric = by_id.get(rid)
    return rubric.display_name if rubric else None


__all__ = [
    "CATEGORY_CATALOG_VERSION",
    "RegionRef",
    "RubricRef",
    "build_catalog_indexes",
    "fetch_category_catalog_for_region",
    "flatten_category_items",
    "get_region_for_point",
    "load_or_fetch_category_catalog",
    "normalize_category_name",
    "official_category_name_for_rubric_id",
    "refresh_dgis_catalog_requested",
    "resolve_configured_place_rubrics",
    "resolve_rubric_from_catalog",
]


fetch_category_catalog_for_region = load_or_fetch_category_catalog
