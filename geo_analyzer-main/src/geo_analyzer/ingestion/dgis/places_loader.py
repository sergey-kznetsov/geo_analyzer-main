from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.enrichment.poi_merge import merge_raw_pois
from geo_analyzer.ingestion.dgis.api_profile import observe_response
from geo_analyzer.ingestion.dgis.category_catalog import (
    official_category_name_for_rubric_id,
    resolve_configured_place_rubrics,
)

logger = get_logger("geo_analyzer.dgis.places")

LOADER_VERSION = "places_loader_v9_api_field_profile_debug_only"


def _cache_path(
    latitude: float,
    longitude: float,
    radius_m: int,
    entries: list[dict[str, Any]],
    region_id: str,
    page_size: int,
    max_pages: int,
) -> Path:
    settings = get_settings()
    payload = {
        "latitude": round(float(latitude), 7),
        "longitude": round(float(longitude), 7),
        "radius_m": int(radius_m),
        "region_id": str(region_id),
        "entries": entries,
        "page_size": int(page_size),
        "max_pages": int(max_pages),
        "loader_version": LOADER_VERSION,
    }
    digest = hashlib.md5(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return settings.cache_dir / "places" / f"{digest}.parquet"


def _load_cached(path: Path) -> gpd.GeoDataFrame | None:
    if not path.exists():
        return None

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        logger.warning("Не удалось прочитать кеш POI %s: %s", path, exc)
        return None

    if "geometry_wkt" not in df.columns:
        return None

    try:
        gdf = gpd.GeoDataFrame(
            df.drop(columns=["geometry_wkt"]),
            geometry=gpd.GeoSeries.from_wkt(df["geometry_wkt"]),
            crs="EPSG:4326",
        )
    except Exception as exc:
        logger.warning("Не удалось восстановить геометрию POI из кеша %s: %s", path, exc)
        return None

    logger.info("POI загружены из кеша: %s (%s)", path, len(gdf))
    return gdf


def _save_cached(gdf: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(gdf.drop(columns=["geometry"], errors="ignore"))
    df["geometry_wkt"] = gdf.geometry.to_wkt()
    df.to_parquet(path, index=False)
    logger.info("POI сохранены в кеш: %s (%s)", path, len(gdf))


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]
    if isinstance(data.get("items"), list):
        return data["items"]
    return []


def _extract_total(data: dict[str, Any]) -> int | None:
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("total", "total_count", "items_count"):
            try:
                value = result.get(key)
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _extract_review_count(item: dict[str, Any]) -> int | None:
    value = item.get("reviews_count")
    try:
        if value is not None and not pd.isna(value):
            return int(float(value))
    except (TypeError, ValueError):
        pass

    reviews = item.get("reviews")
    if isinstance(reviews, dict):
        for key in ["count", "general_count", "total_count", "items_count", "review_count"]:
            value = reviews.get(key)
            try:
                if value is not None and not pd.isna(value):
                    return int(float(value))
            except (TypeError, ValueError):
                pass

    return None


def _extract_rating(item: dict[str, Any]) -> float | None:
    for key in ("rating", "general_rating", "org_rating"):
        value = item.get(key)
        try:
            if value is not None and not pd.isna(value):
                return float(value)
        except (TypeError, ValueError):
            pass

    reviews = item.get("reviews")
    if isinstance(reviews, dict):
        for key in ("rating", "general_rating", "org_rating"):
            value = reviews.get(key)
            try:
                if value is not None and not pd.isna(value):
                    return float(value)
            except (TypeError, ValueError):
                pass

    return None


def _extract_rubrics(item: dict[str, Any], fallback_name: str) -> list[str]:
    rubrics = item.get("rubrics") or []
    result: list[str] = []

    if isinstance(rubrics, list):
        for rubric in rubrics:
            if not isinstance(rubric, dict):
                continue
            name = str(rubric.get("name") or rubric.get("title") or rubric.get("caption") or "").strip()
            if name and name not in result:
                result.append(name)

    if not result and fallback_name:
        result.append(fallback_name)

    return result


def _extract_rubric_ids(item: dict[str, Any], fallback_id: str) -> list[str]:
    rubrics = item.get("rubrics") or []
    result: list[str] = []

    if isinstance(rubrics, list):
        for rubric in rubrics:
            if not isinstance(rubric, dict):
                continue
            rubric_id = str(rubric.get("id", "")).strip()
            if rubric_id and rubric_id not in result:
                result.append(rubric_id)

    if not result and fallback_id:
        result.append(fallback_id)

    return result


def _official_names_for_ids(rubric_ids: list[str], fallback_names: list[str], catalog: dict[str, Any]) -> list[str]:
    result: list[str] = []

    for rubric_id in rubric_ids:
        official = official_category_name_for_rubric_id(rubric_id, catalog)
        if official and official not in result:
            result.append(official)

    for name in fallback_names:
        if name and name not in result:
            result.append(name)

    return result


def _build_rows(
    items: list[dict[str, Any]],
    category_name: str,
    rubric_name: str,
    rubric_id: str,
    region_id: str,
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for item in items:
        point = item.get("point") or {}
        lat = point.get("lat")
        lon = point.get("lon")

        if lat is None or lon is None:
            continue

        api_rubric_names = _extract_rubrics(item, rubric_name or category_name)
        api_rubric_ids = _extract_rubric_ids(item, rubric_id)
        official_names = _official_names_for_ids(api_rubric_ids, api_rubric_names, catalog)
        primary_category = official_names[0] if official_names else rubric_name or category_name

        rows.append(
            {
                "dgis_id": item.get("id"),
                "fid": item.get("external_id"),
                "Название": item.get("name"),
                "Адрес": item.get("address_name") or item.get("full_address_name"),
                "Широта": float(lat),
                "Долгота": float(lon),
                "Рейтинг": _extract_rating(item),
                "Количество_отзывов": _extract_review_count(item),
                "Источник": "2GIS",
                "Категория_2GIS": primary_category,
                "Категория_2GIS_официальная": primary_category,
                "rubric_id": rubric_id,
                "resolved_region_id": region_id,
                "source_category_2gis": primary_category,
                "source_categories_2gis": official_names,
                "rubrics_2gis": official_names,
                "category_groups_2gis": api_rubric_ids,
                "category_validation_status": "official_catalog_match",
                "raw_2gis": item,
                "geometry": Point(float(lon), float(lat)),
            }
        )

    return rows


def _request_items(
    base_url: str,
    api_key: str,
    timeout: int,
    params: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    request_params = dict(params)
    request_params["key"] = api_key

    response = requests.get(base_url, params=request_params, timeout=timeout)

    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        return [], {}

    if isinstance(data, dict):
        observe_response(
            region_id=request_params.get("region_id"),
            source="places_loader",
            object_kind=f"rubric_{request_params.get('rubric_id', 'unknown')}",
            data=data,
            request_params={key: value for key, value in request_params.items() if key != "key"},
        )

    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    code = int(meta.get("code", response.status_code))

    if code >= 400:
        logger.warning(
            "2GIS Places вернул ошибку code=%s params=%s raw=%s",
            code,
            {key: value for key, value in request_params.items() if key != "key"},
            json.dumps(data, ensure_ascii=False)[:800],
        )
        return [], data if isinstance(data, dict) else {}

    response.raise_for_status()
    return _extract_items(data if isinstance(data, dict) else {}), data if isinstance(data, dict) else {}


def _request_rubric_page(
    *,
    latitude: float,
    longitude: float,
    radius: int,
    rubric_id: str,
    region_id: str,
    api_key: str,
    catalog_url: str,
    timeout: int,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    url = f"{catalog_url.rstrip('/')}/3.0/items"

    fields = (
        "items.id,"
        "items.external_id,"
        "items.point,"
        "items.address_name,"
        "items.full_address_name,"
        "items.rubrics,"
        "items.rating,"
        "items.reviews,"
        "items.reviews_count,"
        "items.name"
    )

    point_value = f"{longitude},{latitude}"
    base_params = {
        "rubric_id": rubric_id,
        "region_id": region_id,
        "point": point_value,
        "location": point_value,
        "radius": radius,
        "page": page,
        "page_size": page_size,
        "fields": fields,
        "sort": "distance",
    }

    variants: list[tuple[str, dict[str, Any]]] = [
        ("rubric_id + region_id + point + radius", base_params),
        ("rubric_id + region_id + point + radius + type=branch", dict(base_params, type="branch")),
        (
            "rubric_id + region_id + location",
            {
                "rubric_id": rubric_id,
                "region_id": region_id,
                "location": point_value,
                "page": page,
                "page_size": page_size,
                "fields": fields,
                "sort": "distance",
            },
        ),
    ]

    last_data: dict[str, Any] = {}
    for label, params in variants:
        items, data = _request_items(
            base_url=url,
            api_key=api_key,
            timeout=timeout,
            params=params,
        )
        last_data = data
        if items:
            return items, data, label

    return [], last_data, "no_rubric_variant_returned_items"


def _load_category_by_rubric(
    *,
    latitude: float,
    longitude: float,
    radius_m: int,
    category_name: str,
    rubric_name: str,
    rubric_id: str,
    region_id: str,
    api_key: str,
    catalog_url: str,
    page_size: int,
    max_pages: int,
    timeout: int,
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total: int | None = None
    last_label = ""

    for page in range(1, max_pages + 1):
        items, raw_data, variant_label = _request_rubric_page(
            latitude=latitude,
            longitude=longitude,
            radius=radius_m,
            rubric_id=rubric_id,
            region_id=region_id,
            api_key=api_key,
            catalog_url=catalog_url,
            timeout=timeout,
            page=page,
            page_size=page_size,
        )
        last_label = variant_label

        if total is None:
            total = _extract_total(raw_data)

        if not items:
            if page == 1:
                logger.warning(
                    "2GIS пустой ответ official_category=%s rubric_id=%s raw=%s",
                    category_name,
                    rubric_id,
                    json.dumps(raw_data, ensure_ascii=False)[:800],
                )
            break

        rows.extend(
            _build_rows(
                items=items,
                category_name=category_name,
                rubric_name=rubric_name,
                rubric_id=rubric_id,
                region_id=region_id,
                catalog=catalog,
            )
        )

        logger.info(
            "2GIS official category=%s rubric_id=%s page=%s variant=%s items=%s collected=%s total=%s",
            category_name,
            rubric_id,
            page,
            variant_label,
            len(items),
            len(rows),
            total,
        )

        if total is not None and len(rows) >= total:
            break
        if len(items) < page_size:
            break
        time.sleep(0.18)

    if total is not None and len(rows) < total:
        logger.warning(
            "2GIS category=%s rubric_id=%s: собрано %s из total=%s. Увеличь dgis.places_max_pages. Последний вариант=%s",
            category_name,
            rubric_id,
            len(rows),
            total,
            last_label,
        )

    return rows


def _deduplicate_raw_pois(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df.empty:
        return raw_df

    data = raw_df.copy()

    if "dgis_id" in data.columns:
        key = data["dgis_id"].fillna("").astype(str).str.strip()
        has_key = key.ne("")
        keyed = data[has_key].drop_duplicates(subset=["dgis_id"], keep="first")
        no_key = data[~has_key]
        if no_key.empty:
            return keyed.reset_index(drop=True)
        fallback = no_key.copy()
    else:
        keyed = pd.DataFrame(columns=data.columns)
        fallback = data.copy()

    for column in ["Название", "Адрес", "Широта", "Долгота", "Категория_2GIS"]:
        if column not in fallback.columns:
            fallback[column] = ""

    fallback["_dedupe_key"] = (
        fallback["Название"].fillna("").astype(str).str.lower().str.strip()
        + "|"
        + fallback["Адрес"].fillna("").astype(str).str.lower().str.strip()
        + "|"
        + fallback["Широта"].fillna("").astype(str)
        + "|"
        + fallback["Долгота"].fillna("").astype(str)
        + "|"
        + fallback["Категория_2GIS"].fillna("").astype(str).str.lower().str.strip()
    )
    fallback = fallback.drop_duplicates(subset=["_dedupe_key"], keep="first").drop(columns=["_dedupe_key"], errors="ignore")

    if keyed.empty:
        return fallback.reset_index(drop=True)

    return pd.concat([keyed, fallback], ignore_index=True, sort=False).reset_index(drop=True)


def load_places_near_point(latitude: float, longitude: float, radius_m: int) -> gpd.GeoDataFrame:
    settings = get_settings()
    entries = settings.dgis_place_queries

    if not entries:
        raise RuntimeError("В config.yaml пустой dgis.place_queries.")

    resolved_entries, missing_categories, region, catalog = resolve_configured_place_rubrics(
        entries,
        latitude=float(latitude),
        longitude=float(longitude),
    )

    if missing_categories:
        logger.warning(
            "Рубрики из config.yaml не найдены в официальном rubric/list региона region_id=%s и будут пропущены: %s",
            region.id,
            ", ".join(missing_categories),
        )

    if not resolved_entries:
        raise RuntimeError(
            f"Не найдено ни одной рубрики из config.yaml в официальном rubric/list региона region_id={region.id}. "
            "Проверь названия rubric_name и доступность Categories API."
        )

    page_size = min(int(settings.dgis_places_page_size), 10)
    max_pages = int(settings.dgis_places_max_pages)
    cache_path = _cache_path(latitude, longitude, radius_m, resolved_entries, region.id, page_size, max_pages)

    if settings.use_cache and not settings.refresh_cache:
        cached = _load_cached(cache_path)
        if cached is not None and not cached.empty:
            return cached

    if settings.no_api:
        raise RuntimeError(
            f"Включён --no-api, но кеш POI не найден: {cache_path}. "
            "Сначала один раз запусти без --no-api, чтобы создать кеш."
        )

    rows: list[dict[str, Any]] = []

    for entry in resolved_entries:
        category_name = str(entry.get("category", "")).strip()
        rubric_id = str(entry.get("rubric_id", "")).strip()
        rubric_name = str(entry.get("rubric_name", "")).strip()

        if not category_name or not rubric_id:
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

        if not category_rows:
            logger.warning(
                "2GIS не вернул POI для официальной рубрики region_id=%s category=%s rubric_id=%s",
                region.id,
                category_name,
                rubric_id,
            )
            continue

        rows.extend(category_rows)

    if not rows:
        raise RuntimeError(
            f"2GIS Places не вернул ни одного POI по официальным rubric_id региона region_id={region.id}. "
            "Проверь ключ 2GIS, radius_m и наличие выбранных рубрик рядом с точкой."
        )

    raw_df = pd.DataFrame(rows)
    raw_df = _deduplicate_raw_pois(raw_df)

    try:
        merged_df = merge_raw_pois(raw_df)
    except Exception as exc:
        logger.warning("merge_raw_pois failed, используем внутреннюю дедупликацию: %s", exc)
        merged_df = raw_df

    merged_df = _deduplicate_raw_pois(pd.DataFrame(merged_df))

    gdf = gpd.GeoDataFrame(
        merged_df,
        geometry="geometry",
        crs="EPSG:4326",
    )

    if settings.use_cache:
        _save_cached(gdf, cache_path)

    logger.info(
        "2GIS Places official runtime region mode: region_id=%s loaded_rows=%s deduplicated_poi=%s radius_m=%s",
        region.id,
        len(rows),
        len(gdf),
        radius_m,
    )

    return gdf
