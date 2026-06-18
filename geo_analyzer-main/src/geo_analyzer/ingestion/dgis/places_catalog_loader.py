from __future__ import annotations

"""Load 2GIS POI by the complete official regional rubric catalog.

The legacy loader used a configured list of business categories from config.yaml.
This module uses 2GIS as the source of truth: it resolves the runtime region,
loads the complete official rubric catalog for that region, queries every rubric
inside the analysis radius, deduplicates objects, adds non-rubric typed objects
such as public transport stops, and only then enriches object cards by id.

Technical fields stay in debug profiles/dictionaries; Excel gets only normalized
business columns downstream.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.enrichment.poi_merge import merge_raw_pois
from geo_analyzer.ingestion.dgis.category_catalog import (
    RegionRef,
    build_catalog_indexes,
    get_region_for_point,
    load_or_fetch_category_catalog,
    refresh_dgis_catalog_requested,
)
from geo_analyzer.ingestion.dgis.places_loader import (
    _deduplicate_raw_pois,
    _load_cached,
    _load_category_by_rubric,
    _save_cached,
)
from geo_analyzer.ingestion.dgis.places_enriched_loader import (
    _dedupe_gdf,
    _fetch_station_items,
    _station_items_to_gdf,
    enrich_place_cards,
    load_places_near_point as _load_configured_enriched_places,
)

logger = get_logger("geo_analyzer.dgis.places_catalog")

ALL_RUBRICS_LOADER_VERSION = "places_catalog_loader_v1_all_official_rubrics"
ALL_RUBRICS_SLEEP_SEC = 0.04


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "да", "on"}


def _rubric_scope(settings: Any) -> str:
    env = os.getenv("GEO_ANALYZER_DGIS_PLACE_RUBRIC_SCOPE")
    if env:
        return env.strip().lower()
    dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
    return str(dgis_config.get("place_rubric_scope") or "all").strip().lower()


def all_official_rubrics_enabled(settings: Any | None = None) -> bool:
    settings = settings or get_settings()
    return _rubric_scope(settings) in {"all", "all_official", "official", "catalog", "2gis"}


def _display_rubric(row: dict[str, Any]) -> str:
    return str(
        row.get("title")
        or row.get("caption")
        or row.get("name")
        or row.get("keyword")
        or row.get("alias")
        or row.get("id")
        or ""
    ).strip()


def _as_int(value: Any) -> int:
    try:
        if value is None or pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _rubric_has_catalog_objects(row: dict[str, Any]) -> bool:
    # 2GIS rubric/list contains regional counters. Rows without any counters are
    # usually grouping nodes or empty regional categories. Keep unknown counters
    # because some API responses omit them.
    counters = [row.get("branch_count"), row.get("org_count"), row.get("geo_count")]
    if all(value in (None, "") for value in counters):
        return True
    return sum(_as_int(value) for value in counters) > 0


def all_catalog_rubric_entries(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Return query entries for every usable official rubric in a region catalog."""
    entries: list[dict[str, Any]] = []
    used: set[str] = set()
    items = catalog.get("items", []) if isinstance(catalog, dict) else []
    if not isinstance(items, list):
        return []

    for row in items:
        if not isinstance(row, dict):
            continue
        rubric_id = str(row.get("id") or "").strip()
        name = _display_rubric(row)
        if not rubric_id or not name or rubric_id in used:
            continue
        if not _rubric_has_catalog_objects(row):
            continue
        used.add(rubric_id)
        entries.append(
            {
                "category": name,
                "rubric_name": name,
                "rubric_id": rubric_id,
                "region_id": str(row.get("region_id") or ""),
                "official_name": str(row.get("name") or ""),
                "official_title": str(row.get("title") or ""),
                "official_caption": str(row.get("caption") or ""),
                "official_alias": str(row.get("alias") or ""),
                "official_keyword": str(row.get("keyword") or ""),
                "official_parent_id": str(row.get("parent_id") or ""),
                "official_type": str(row.get("type") or ""),
                "catalog_branch_count": row.get("branch_count"),
                "catalog_org_count": row.get("org_count"),
                "catalog_geo_count": row.get("geo_count"),
            }
        )

    # Query leaf and small rubrics before broad grouping nodes. This improves early
    # coverage if a user sets a manual maximum, but default mode still walks all.
    _, _by_name = build_catalog_indexes(catalog)
    entries.sort(
        key=lambda entry: (
            0 if entry.get("official_parent_id") else 1,
            -(_as_int(entry.get("catalog_branch_count")) + _as_int(entry.get("catalog_geo_count"))),
            str(entry.get("category") or "").lower(),
        )
    )
    return entries


def _max_all_rubrics(settings: Any) -> int:
    env = os.getenv("GEO_ANALYZER_DGIS_ALL_RUBRICS_MAX")
    if env is None:
        dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
        env = dgis_config.get("all_rubrics_max", 0)
    try:
        return max(0, int(env or 0))
    except (TypeError, ValueError):
        return 0


def _cache_path(latitude: float, longitude: float, radius_m: int, region_id: str, entries: list[dict[str, Any]], page_size: int, max_pages: int) -> Path:
    settings = get_settings()
    payload = {
        "version": ALL_RUBRICS_LOADER_VERSION,
        "latitude": round(float(latitude), 7),
        "longitude": round(float(longitude), 7),
        "radius_m": int(radius_m),
        "region_id": str(region_id),
        "rubric_ids": [str(entry.get("rubric_id") or "") for entry in entries],
        "page_size": int(page_size),
        "max_pages": int(max_pages),
        "catalog_refresh_requested": refresh_dgis_catalog_requested(),
    }
    digest = hashlib.md5(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return settings.cache_dir / "places_all_rubrics" / f"{digest}.parquet"


def _resolve_region_and_catalog(latitude: float, longitude: float, region_id: str | None, settings: Any) -> tuple[RegionRef, dict[str, Any]]:
    dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
    locale = str(dgis_config.get("category_catalog_locale") or "ru_RU").strip() or "ru_RU"
    if region_id:
        region = RegionRef(id=str(region_id), name="runtime_geocoder")
    else:
        region = get_region_for_point(float(latitude), float(longitude))
    if not region.id:
        raise RuntimeError("Не удалось определить region_id 2GIS. Нельзя загрузить все официальные рубрики региона.")
    catalog = load_or_fetch_category_catalog(region_id=region.id, locale=locale, refresh=False)
    return region, catalog


def _append_station_objects(base: gpd.GeoDataFrame, *, latitude: float, longitude: float, radius_m: int, region_id: str, settings: Any) -> gpd.GeoDataFrame:
    stations = _station_items_to_gdf(
        _fetch_station_items(latitude=latitude, longitude=longitude, radius_m=radius_m, region_id=region_id, settings=settings),
        region_id=region_id,
    )
    if stations is None or stations.empty:
        return base
    combined = pd.concat([base, stations], ignore_index=True, sort=False) if base is not None and not base.empty else stations
    result = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    result = _dedupe_gdf(result)
    logger.info("2GIS station objects added to all-rubrics POI dataset: stations=%s total=%s", len(stations), len(result))
    return result


def load_all_official_places_near_point(latitude: float, longitude: float, radius_m: int, region_id: str | None = None) -> gpd.GeoDataFrame:
    """Load POI using every official rubric from the runtime 2GIS region catalog."""
    settings = get_settings()
    if not all_official_rubrics_enabled(settings):
        return _load_configured_enriched_places(latitude, longitude, radius_m, region_id=region_id)

    region, catalog = _resolve_region_and_catalog(float(latitude), float(longitude), region_id, settings)
    entries = all_catalog_rubric_entries(catalog)
    limit = _max_all_rubrics(settings)
    if limit:
        entries = entries[:limit]
    if not entries:
        raise RuntimeError(f"2GIS rubric/list региона region_id={region.id} не содержит рубрик для загрузки POI.")

    page_size = min(int(settings.dgis_places_page_size), 10)
    max_pages = int(settings.dgis_places_max_pages)
    cache_path = _cache_path(latitude, longitude, radius_m, region.id, entries, page_size, max_pages)

    if settings.use_cache and not settings.refresh_cache:
        cached = _load_cached(cache_path)
        if cached is not None and not cached.empty:
            return cached

    if settings.no_api:
        raise RuntimeError(
            f"Включён --no-api, но кеш POI по всем рубрикам не найден: {cache_path}. "
            "Сначала один раз запусти без --no-api, чтобы создать кеш."
        )

    rows: list[dict[str, Any]] = []
    empty_count = 0
    loaded_rubrics = 0

    for index, entry in enumerate(entries, start=1):
        category_name = str(entry.get("category") or "").strip()
        rubric_id = str(entry.get("rubric_id") or "").strip()
        rubric_name = str(entry.get("rubric_name") or category_name).strip()
        if not rubric_id or not category_name:
            continue
        category_rows = _load_category_by_rubric(
            latitude=float(latitude),
            longitude=float(longitude),
            radius_m=int(radius_m),
            category_name=category_name,
            rubric_name=rubric_name,
            rubric_id=rubric_id,
            region_id=region.id,
            api_key=settings.dgis_api_key,
            catalog_url=settings.dgis_catalog_url,
            page_size=page_size,
            max_pages=max_pages,
            timeout=settings.dgis_timeout,
            catalog=catalog,
        )
        if category_rows:
            loaded_rubrics += 1
            rows.extend(category_rows)
        else:
            empty_count += 1
        if index % 50 == 0:
            logger.info(
                "2GIS all-rubrics progress: region_id=%s checked=%s/%s loaded_rubrics=%s raw_rows=%s empty=%s",
                region.id,
                index,
                len(entries),
                loaded_rubrics,
                len(rows),
                empty_count,
            )
        time.sleep(ALL_RUBRICS_SLEEP_SEC)

    if not rows:
        raise RuntimeError(
            f"2GIS Places не вернул ни одного POI при обходе всех официальных рубрик region_id={region.id}. "
            "Проверь ключ, радиус и доступность Places API."
        )

    raw_df = _deduplicate_raw_pois(pd.DataFrame(rows))
    try:
        merged_df = merge_raw_pois(raw_df)
    except Exception as exc:
        logger.warning("merge_raw_pois failed in all-rubrics loader, используем внутреннюю дедупликацию: %s", exc)
        merged_df = raw_df
    merged_df = _deduplicate_raw_pois(pd.DataFrame(merged_df))

    gdf = gpd.GeoDataFrame(merged_df, geometry="geometry", crs="EPSG:4326")
    gdf = _append_station_objects(gdf, latitude=float(latitude), longitude=float(longitude), radius_m=int(radius_m), region_id=region.id, settings=settings)
    gdf = enrich_place_cards(gdf)

    if settings.use_cache:
        _save_cached(gdf, cache_path)

    logger.info(
        "2GIS all official rubrics loaded: region_id=%s catalog_rubrics=%s checked_rubrics=%s loaded_rubrics=%s raw_rows=%s deduplicated_poi=%s radius_m=%s",
        region.id,
        len(catalog.get("items", [])) if isinstance(catalog.get("items"), list) else 0,
        len(entries),
        loaded_rubrics,
        len(rows),
        len(gdf),
        radius_m,
    )
    return gdf


load_places_near_point = load_all_official_places_near_point

__all__ = [
    "all_catalog_rubric_entries",
    "all_official_rubrics_enabled",
    "load_all_official_places_near_point",
    "load_places_near_point",
]
