from __future__ import annotations

"""Runtime patch for resolving 2GIS region_id without deprecated /region/get point calls.

2GIS Regions API resolves regions through /2.0/region/search by text query.
For analysed addresses the most reliable source is the geocoder result field
``items.region_id``. The pipeline stores that value in the environment for the
current run. For coordinate-only runs this module attempts a lightweight
coordinate geocode probe and falls back to config only when the API cannot give a
region.
"""

import os
from typing import Any

import requests

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis import category_catalog as _catalog

logger = get_logger("geo_analyzer.dgis.region_runtime_patch")

ENV_REGION_ID = "GEO_ANALYZER_CURRENT_DGIS_REGION_ID"
ENV_REGION_NAME = "GEO_ANALYZER_CURRENT_DGIS_REGION_NAME"


def _extract_items(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return [item for item in result["items"] if isinstance(item, dict)]
    if isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def _extract_region_from_item(item: dict[str, Any]) -> _catalog.RegionRef | None:
    region_id = str(item.get("region_id") or "").strip()
    region_name = ""
    adm_div = item.get("adm_div")
    if isinstance(adm_div, list):
        for part in reversed(adm_div):
            if not isinstance(part, dict):
                continue
            if not region_id:
                region_id = str(part.get("region_id") or part.get("region_id_2gis") or part.get("id") or "").strip()
            if not region_name:
                region_name = str(part.get("name") or "").strip()
            if region_id:
                break
    if region_id:
        return _catalog.RegionRef(id=region_id, name=region_name)
    return None


def _probe_region_by_coordinates(latitude: float, longitude: float) -> _catalog.RegionRef | None:
    settings = get_settings()
    if settings.no_api or not settings.dgis_api_key:
        return None

    point = f"{float(longitude)},{float(latitude)}"
    base = settings.dgis_catalog_url.rstrip("/")
    variants = [
        (
            f"{base}/3.0/items/geocode",
            {
                "q": point,
                "location": point,
                "type": "coordinates,adm_div,building",
                "fields": "items.region_id,items.adm_div,items.point,items.name,items.full_name",
                "page_size": 5,
            },
        ),
        (
            f"{base}/3.0/items",
            {
                "location": point,
                "point": point,
                "radius": 1000,
                "type": "adm_div,building",
                "fields": "items.region_id,items.adm_div,items.point,items.name,items.full_name",
                "page_size": 5,
            },
        ),
    ]

    for url, params in variants:
        request_params = dict(params)
        request_params["key"] = settings.dgis_api_key
        try:
            response = requests.get(url, params=request_params, timeout=settings.dgis_timeout)
            data = response.json()
            _catalog._raise_if_auth_error(data if isinstance(data, dict) else {}, status_code=response.status_code, stage="region coordinate probe")
        except RuntimeError:
            raise
        except Exception as exc:
            logger.debug("2GIS region coordinate probe failed url=%s point=%s: %s", url, point, exc)
            continue

        for item in _extract_items(data if isinstance(data, dict) else {}):
            region = _extract_region_from_item(item)
            if region and region.id:
                return region
    return None


def get_region_for_point(latitude: float | None, longitude: float | None) -> _catalog.RegionRef:
    settings = get_settings()
    fallback = _catalog.RegionRef(id=settings.dgis_region_id or "", name="config_fallback")

    env_region_id = str(os.getenv(ENV_REGION_ID) or "").strip()
    if env_region_id:
        return _catalog.RegionRef(id=env_region_id, name=str(os.getenv(ENV_REGION_NAME) or "runtime_geocoder"))

    if latitude is None or longitude is None:
        return fallback

    try:
        probed = _probe_region_by_coordinates(float(latitude), float(longitude))
        if probed and probed.id:
            return probed
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("2GIS region probe failed for point=%s,%s: %s", latitude, longitude, exc)

    logger.warning(
        "2GIS region_id не определён по координатам %s,%s; используется fallback region_id=%s. "
        "Для адресных запусков region_id должен приходить из geocoder items.region_id.",
        latitude,
        longitude,
        fallback.id,
    )
    return fallback


def apply_patch() -> None:
    _catalog.get_region_for_point = get_region_for_point


apply_patch()

__all__ = ["ENV_REGION_ID", "ENV_REGION_NAME", "apply_patch", "get_region_for_point"]
