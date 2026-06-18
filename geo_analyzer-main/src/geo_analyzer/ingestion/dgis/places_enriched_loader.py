from __future__ import annotations

"""2GIS POI loader with detail-card enrichment.

The base loader collects POI by official rubric_id. This wrapper keeps that
behaviour, adds non-rubric infrastructure objects that 2GIS exposes by object
``type`` (public transport stations/platforms), then fetches every available
object card by ``id`` before the POI is used by classification, maps, reports and
scoring. Raw technical data is saved only to debug profiles and dictionaries.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.api_dictionary_logger import observe_object_dictionary
from geo_analyzer.ingestion.dgis.api_profile import observe_items, observe_response
from geo_analyzer.ingestion.dgis.places_loader import load_places_near_point as _load_places_near_point_base

logger = get_logger("geo_analyzer.dgis.places_enriched")

ENRICHED_LOADER_VERSION = "places_enriched_loader_v2_detail_cards_and_station_types"
DETAIL_SLEEP_SEC = 0.04
STATION_PAGE_SIZE = 10
STATION_MAX_PAGES = 30

PLACE_DETAIL_FIELDS = (
    "items.id,items.external_id,items.type,items.subtype,items.point,items.geometry,"
    "items.geometry.hull,items.geometry.centroid,items.geometry.selection,"
    "items.address_name,items.full_address_name,items.rubrics,items.name,items.rating,"
    "items.reviews,items.reviews_count,items.schedule,items.attribute_groups,items.context,"
    "items.purpose,items.purpose_name,items.links,items.description,items.statistics,"
    "items.adm_div,items.contact_groups,items.flags,items.region_id,items.poi_category"
)

STATION_FIELDS = (
    PLACE_DETAIL_FIELDS
    + ",items.routes,items.directions,items.platforms,items.station_id,items.access_name"
)

DETAIL_COLUMNS = [
    "detail_card_checked_2gis",
    "object_type_2gis",
    "object_subtype_2gis",
    "attribute_groups",
    "schedule_2gis",
    "context_2gis",
    "purpose_2gis",
    "purpose_name_2gis",
    "links_2gis",
    "description_2gis",
    "statistics_2gis",
    "adm_div_2gis",
    "contact_groups_2gis",
    "flags_2gis",
    "routes_2gis",
    "directions_2gis",
    "platforms_2gis",
    "station_id_2gis",
]


def _enabled(settings: Any) -> bool:
    env = os.getenv("GEO_ANALYZER_ENRICH_PLACE_CARDS")
    if env is not None:
        return str(env).strip().lower() not in {"0", "false", "no", "n", "нет"}
    return bool(settings.config.get("dgis", {}).get("enrich_place_cards", True))


def _station_loader_enabled(settings: Any) -> bool:
    env = os.getenv("GEO_ANALYZER_LOAD_DGIS_STATIONS")
    if env is not None:
        return str(env).strip().lower() not in {"0", "false", "no", "n", "нет"}
    return bool(settings.config.get("dgis", {}).get("load_station_objects", True))


def _extract_items(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return [item for item in result["items"] if isinstance(item, dict)]
    if isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def _detail_cache_path(settings: Any, region_id: str, item_id: str) -> Path:
    payload = {
        "version": ENRICHED_LOADER_VERSION,
        "region_id": str(region_id),
        "item_id": str(item_id),
        "fields": PLACE_DETAIL_FIELDS,
    }
    digest = hashlib.md5(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return settings.cache_dir / "dgis_item_cards" / str(region_id or "unknown") / f"{digest}.json"


def _load_cached_detail(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_cached_detail(path: Path, detail: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(detail, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.debug("Не удалось сохранить кеш карточки 2GIS %s: %s", path, exc)


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if key != "key"}


def _request_detail(item_id: str, *, region_id: str, source_kind: str, settings: Any) -> dict[str, Any] | None:
    if not item_id or settings.no_api:
        return None

    cache_path = _detail_cache_path(settings, region_id, item_id)
    if settings.use_cache and not settings.refresh_cache:
        cached = _load_cached_detail(cache_path)
        if cached:
            return cached

    base = settings.dgis_catalog_url.rstrip("/")
    variants = [
        (f"{base}/3.0/items/byid", {"id": item_id, "region_id": region_id, "fields": PLACE_DETAIL_FIELDS, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items/byid", {"id": item_id, "fields": PLACE_DETAIL_FIELDS, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items", {"id": item_id, "region_id": region_id, "fields": PLACE_DETAIL_FIELDS, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items", {"id": item_id, "fields": PLACE_DETAIL_FIELDS, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items/byid", {"id": item_id, "region_id": region_id, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items/byid", {"id": item_id, "key": settings.dgis_api_key}),
    ]

    for url, params in variants:
        try:
            response = requests.get(url, params=params, timeout=settings.dgis_timeout)
            data = response.json()
        except Exception as exc:
            logger.debug("Не удалось получить карточку 2GIS id=%s: %s", item_id, exc)
            continue

        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        code = int(meta.get("code", response.status_code))
        if code >= 400:
            logger.debug("2GIS detail card вернул code=%s id=%s params=%s", code, item_id, _clean_params(params))
            continue

        items = _extract_items(data if isinstance(data, dict) else {})
        if not items:
            continue

        observe_items(
            region_id=region_id,
            source="places_loader_detail_card",
            object_kind=source_kind,
            items=items,
            request_params=_clean_params(params),
            raw_response=data if isinstance(data, dict) else None,
        )
        observe_object_dictionary(
            region_id=region_id,
            source="places_loader_detail_card",
            object_kind=source_kind,
            items=items,
            request_params=_clean_params(params),
        )
        detail = items[0]
        if settings.use_cache:
            _save_cached_detail(cache_path, detail)
        return detail

    return None


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _merge_item(base_item: Any, detail: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(base_item) if isinstance(base_item, dict) else {}
    if not isinstance(detail, dict) or not detail:
        return base
    merged = dict(base)
    for key, value in detail.items():
        if _missing(value):
            continue
        if key not in merged or _missing(merged.get(key)):
            merged[key] = value
        elif isinstance(merged.get(key), dict) and isinstance(value, dict):
            nested = dict(merged[key])
            for child_key, child_value in value.items():
                if child_key not in nested or _missing(nested.get(child_key)):
                    nested[child_key] = child_value
            merged[key] = nested
    return merged


def _point_from_item(item: dict[str, Any]) -> Point | None:
    point = item.get("point")
    if isinstance(point, dict) and not _missing(point.get("lat")) and not _missing(point.get("lon")):
        try:
            return Point(float(point.get("lon")), float(point.get("lat")))
        except (TypeError, ValueError):
            return None
    geometry = item.get("geometry")
    if isinstance(geometry, dict):
        for key in ("centroid", "selection", "hull"):
            child = geometry.get(key)
            if isinstance(child, dict) and not _missing(child.get("lat")) and not _missing(child.get("lon")):
                try:
                    return Point(float(child.get("lon")), float(child.get("lat")))
                except (TypeError, ValueError):
                    continue
    return None


def _apply_detail_to_row(gdf: gpd.GeoDataFrame, index: Any, detail: dict[str, Any], raw_item: dict[str, Any]) -> None:
    merged = _merge_item(raw_item, detail)
    for column in DETAIL_COLUMNS:
        if column not in gdf.columns:
            gdf[column] = None

    gdf.at[index, "detail_card_checked_2gis"] = True
    gdf.at[index, "raw_2gis"] = merged
    gdf.at[index, "object_type_2gis"] = merged.get("type")
    gdf.at[index, "object_subtype_2gis"] = merged.get("subtype")
    gdf.at[index, "attribute_groups"] = merged.get("attribute_groups")
    gdf.at[index, "schedule_2gis"] = merged.get("schedule")
    gdf.at[index, "context_2gis"] = merged.get("context")
    gdf.at[index, "purpose_2gis"] = merged.get("purpose")
    gdf.at[index, "purpose_name_2gis"] = merged.get("purpose_name")
    gdf.at[index, "links_2gis"] = merged.get("links")
    gdf.at[index, "description_2gis"] = merged.get("description")
    gdf.at[index, "statistics_2gis"] = merged.get("statistics")
    gdf.at[index, "adm_div_2gis"] = merged.get("adm_div")
    gdf.at[index, "contact_groups_2gis"] = merged.get("contact_groups")
    gdf.at[index, "flags_2gis"] = merged.get("flags")
    gdf.at[index, "routes_2gis"] = merged.get("routes")
    gdf.at[index, "directions_2gis"] = merged.get("directions")
    gdf.at[index, "platforms_2gis"] = merged.get("platforms")
    gdf.at[index, "station_id_2gis"] = merged.get("station_id")

    if _missing(gdf.at[index, "Название"]) and merged.get("name"):
        gdf.at[index, "Название"] = merged.get("name")
    if _missing(gdf.at[index, "Адрес"]) and (merged.get("address_name") or merged.get("full_address_name")):
        gdf.at[index, "Адрес"] = merged.get("address_name") or merged.get("full_address_name")

    detail_point = _point_from_item(merged)
    if detail_point is not None:
        gdf.at[index, "geometry"] = detail_point
        gdf.at[index, "Широта"] = float(detail_point.y)
        gdf.at[index, "Долгота"] = float(detail_point.x)


def _fetch_station_items(*, latitude: float, longitude: float, radius_m: int, region_id: str, settings: Any) -> list[dict[str, Any]]:
    if settings.no_api or not settings.dgis_api_key or not _station_loader_enabled(settings):
        return []

    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    point = f"{float(longitude)},{float(latitude)}"
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for object_type in ("station", "station_platform"):
        for page in range(1, STATION_MAX_PAGES + 1):
            params = {
                "key": settings.dgis_api_key,
                "region_id": str(region_id or ""),
                "type": object_type,
                "point": point,
                "location": point,
                "radius": int(radius_m),
                "page": page,
                "page_size": STATION_PAGE_SIZE,
                "fields": STATION_FIELDS,
                "sort": "distance",
            }
            if not params["region_id"]:
                params.pop("region_id", None)
            try:
                response = requests.get(url, params=params, timeout=settings.dgis_timeout)
                data = response.json()
            except Exception as exc:
                logger.debug("2GIS station loader failed type=%s page=%s: %s", object_type, page, exc)
                break

            if isinstance(data, dict):
                observe_response(
                    region_id=str(region_id or "unknown"),
                    source="places_loader_type_query_response",
                    object_kind=object_type,
                    data=data,
                    request_params=_clean_params(params),
                )
            meta = data.get("meta", {}) if isinstance(data, dict) else {}
            code = int(meta.get("code", response.status_code))
            if code == 404:
                break
            if code >= 400:
                logger.debug("2GIS station loader code=%s type=%s params=%s", code, object_type, _clean_params(params))
                break

            items = _extract_items(data if isinstance(data, dict) else {})
            if not items:
                break
            for item in items:
                item_id = str(item.get("id") or "").strip()
                dedupe_key = item_id or json.dumps(item.get("point") or {}, sort_keys=True, ensure_ascii=False)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                collected.append(item)
            if len(items) < STATION_PAGE_SIZE:
                break
            time.sleep(0.08)

    if collected:
        observe_items(
            region_id=str(region_id or "unknown"),
            source="places_loader_type_query",
            object_kind="station_objects",
            items=collected,
            request_params={"type": "station,station_platform", "radius_m": int(radius_m)},
        )
        observe_object_dictionary(
            region_id=str(region_id or "unknown"),
            source="places_loader_type_query",
            object_kind="station_objects",
            items=collected,
            request_params={"type": "station,station_platform", "radius_m": int(radius_m)},
        )
    return collected


def _station_items_to_gdf(items: list[dict[str, Any]], *, region_id: str) -> gpd.GeoDataFrame:
    rows: list[dict[str, Any]] = []
    for item in items:
        point = _point_from_item(item)
        if point is None:
            continue
        rows.append(
            {
                "dgis_id": item.get("id"),
                "fid": item.get("external_id"),
                "Название": item.get("name") or "Остановка общественного транспорта",
                "Адрес": item.get("address_name") or item.get("full_address_name"),
                "Категория_2GIS": "Остановки общественного транспорта",
                "Категория_2GIS_официальная": "Остановки общественного транспорта",
                "source_category_2gis": "Остановки общественного транспорта",
                "source_categories_2gis": ["Остановки общественного транспорта"],
                "rubrics_2gis": ["Остановки общественного транспорта"],
                "category_groups_2gis": ["type:station"],
                "rubric_id": "type:station",
                "resolved_region_id": str(region_id or item.get("region_id") or ""),
                "object_type_2gis": item.get("type") or "station",
                "object_subtype_2gis": item.get("subtype"),
                "routes_2gis": item.get("routes"),
                "directions_2gis": item.get("directions"),
                "platforms_2gis": item.get("platforms"),
                "station_id_2gis": item.get("station_id"),
                "raw_2gis": item,
                "Широта": float(point.y),
                "Долгота": float(point.x),
                "geometry": point,
            }
        )
    if not rows:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _dedupe_gdf(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        return gdf
    data = gdf.copy()
    if "dgis_id" in data.columns:
        keyed = data["dgis_id"].fillna("").astype(str).str.strip()
        with_id = data[keyed.ne("")].drop_duplicates(subset=["dgis_id"], keep="first")
        without_id = data[keyed.eq("")]
        data = pd.concat([with_id, without_id], ignore_index=True, sort=False)
    return gpd.GeoDataFrame(data, geometry="geometry", crs=getattr(gdf, "crs", "EPSG:4326") or "EPSG:4326")


def enrich_place_cards(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    settings = get_settings()
    if gdf is None or gdf.empty or not _enabled(settings):
        return gdf

    data = gdf.copy()
    if "dgis_id" not in data.columns:
        return data
    if "raw_2gis" not in data.columns:
        data["raw_2gis"] = None

    details_by_id: dict[str, dict[str, Any] | None] = {}
    enriched_items: list[dict[str, Any]] = []

    for index, row in data.iterrows():
        item_id = str(row.get("dgis_id") or "").strip()
        if not item_id:
            continue
        region_id = str(row.get("resolved_region_id") or row.get("region_id") or "unknown").strip() or "unknown"
        rubric_id = str(row.get("rubric_id") or row.get("object_type_2gis") or "unknown").strip() or "unknown"
        source_kind = f"{rubric_id}_detail" if rubric_id.startswith("type:") else f"rubric_{rubric_id}_detail"
        raw_item = row.get("raw_2gis") if isinstance(row.get("raw_2gis"), dict) else {}

        if item_id not in details_by_id:
            details_by_id[item_id] = _request_detail(item_id, region_id=region_id, source_kind=source_kind, settings=settings)
            time.sleep(DETAIL_SLEEP_SEC)
        detail = details_by_id.get(item_id)
        if isinstance(detail, dict) and detail:
            _apply_detail_to_row(data, index, detail, raw_item)
            enriched_items.append(_merge_item(raw_item, detail))

    if enriched_items:
        region_id = str(data["resolved_region_id"].dropna().astype(str).iloc[0]) if "resolved_region_id" in data.columns and data["resolved_region_id"].notna().any() else "unknown"
        observe_object_dictionary(
            region_id=region_id,
            source="places_loader_enriched_dataset",
            object_kind="all_poi_detail_cards",
            items=enriched_items,
            request_params={"loader_version": ENRICHED_LOADER_VERSION, "items": len(enriched_items)},
        )
        logger.info("2GIS detail-card enrichment: enriched_poi=%s total_poi=%s", len(enriched_items), len(data))

    return gpd.GeoDataFrame(data, geometry="geometry", crs=getattr(gdf, "crs", "EPSG:4326") or "EPSG:4326")


def load_places_near_point(latitude: float, longitude: float, radius_m: int, region_id: str | None = None) -> gpd.GeoDataFrame:
    base = _load_places_near_point_base(latitude, longitude, radius_m)
    settings = get_settings()
    resolved_region_id = str(region_id or "").strip()
    if not resolved_region_id and base is not None and not base.empty and "resolved_region_id" in base.columns:
        values = base["resolved_region_id"].dropna().astype(str)
        if not values.empty:
            resolved_region_id = str(values.iloc[0]).strip()

    stations = _station_items_to_gdf(
        _fetch_station_items(latitude=latitude, longitude=longitude, radius_m=radius_m, region_id=resolved_region_id, settings=settings),
        region_id=resolved_region_id,
    )
    if stations is not None and not stations.empty:
        combined = pd.concat([base, stations], ignore_index=True, sort=False) if base is not None and not base.empty else stations
        base = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
        base = _dedupe_gdf(base)
        logger.info("2GIS station objects added to POI dataset: stations=%s total=%s", len(stations), len(base))

    return enrich_place_cards(base)


__all__ = ["PLACE_DETAIL_FIELDS", "STATION_FIELDS", "enrich_place_cards", "load_places_near_point"]
