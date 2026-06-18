from __future__ import annotations

import re
from typing import Any

import geopandas as gpd
import pandas as pd


_DEF_COLUMNS = [
    "name",
    "category",
    "source",
    "source_category",
    "address",
    "opening_hours",
    "rating",
    "reviews_count",
    "lat",
    "lon",
    "geometry",
]


def _is_empty(value: Any) -> bool:
    if value is None:
        return True

    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass

    return str(value).strip() == ""


def _safe_text(value: Any) -> str:
    if _is_empty(value):
        return ""

    return str(value).strip()


def _normalize_name(value: Any) -> str:
    text = _safe_text(value).lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^\w\sа-я-]", "", text)


def _ensure_schema(
    gdf: gpd.GeoDataFrame | None,
    default_source: str,
) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame(
            columns=_DEF_COLUMNS,
            geometry="geometry",
            crs="EPSG:4326",
        )

    result = gdf.copy()

    if result.crs is None:
        result = result.set_crs(epsg=4326)
    elif str(result.crs).upper() != "EPSG:4326":
        result = result.to_crs(epsg=4326)

    for column in _DEF_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA

    result["source"] = result["source"].apply(
        lambda value: default_source if _is_empty(value) else value
    )

    if "source_category" in result.columns:
        result["source_category"] = result.apply(
            lambda row: row["category"] if _is_empty(row.get("source_category")) else row.get("source_category"),
            axis=1,
        )

    return result[_DEF_COLUMNS].copy()


def spatial_deduplicate_sources(
    osm_gdf: gpd.GeoDataFrame,
    yandex_gdf: gpd.GeoDataFrame,
    distance_threshold_m: float = 35.0,
) -> gpd.GeoDataFrame:
    """Объединяет OSM и Яндекс, убирая пространственные дубли.

    Приоритет остаётся за Яндексом. Функция оставлена для совместимости
    со старым кодом и тестами; основной пайплайн сейчас работает через 2GIS.
    """
    osm = _ensure_schema(osm_gdf, default_source="osm")
    yandex = _ensure_schema(yandex_gdf, default_source="yandex")

    if osm.empty and yandex.empty:
        return _ensure_schema(gpd.GeoDataFrame(), default_source="unknown")

    if osm.empty:
        return yandex.reset_index(drop=True)

    if yandex.empty:
        return osm.reset_index(drop=True)

    osm_proj = osm.to_crs(epsg=3857)
    yandex_proj = yandex.to_crs(epsg=3857)

    joined = gpd.sjoin_nearest(
        osm_proj,
        yandex_proj[["name", "category", "geometry"]],
        how="left",
        max_distance=distance_threshold_m,
        distance_col="distance_m",
        lsuffix="osm",
        rsuffix="yandex",
    )

    duplicate_osm_indices: set[int] = set()

    for osm_index, row in joined.iterrows():
        if pd.isna(row.get("index_yandex")):
            continue

        osm_name = _normalize_name(row.get("name_osm"))
        yandex_name = _normalize_name(row.get("name_yandex"))

        osm_category = _safe_text(row.get("category_osm")).lower()
        yandex_category = _safe_text(row.get("category_yandex")).lower()

        same_name = bool(osm_name and yandex_name and osm_name == yandex_name)
        same_category = bool(osm_category and yandex_category and osm_category == yandex_category)
        unnamed_but_close = bool((not osm_name or not yandex_name) and same_category)

        if same_name or same_category or unnamed_but_close:
            duplicate_osm_indices.add(osm_index)

    osm_unique = osm.loc[~osm.index.isin(duplicate_osm_indices)].copy()
    merged = pd.concat([yandex, osm_unique], ignore_index=True)

    return gpd.GeoDataFrame(
        merged,
        geometry="geometry",
        crs="EPSG:4326",
    )