from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import requests

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings

logger = get_logger("geo_analyzer.dgis.routing")

ROUTING_CACHE_VERSION = "routing_v2_city_center"


def _cache_path(
    origin_latitude: float,
    origin_longitude: float,
    center_latitude: float | None,
    center_longitude: float | None,
    center_name: str | None,
) -> Path:
    settings = get_settings()

    payload = {
        "origin_latitude": round(float(origin_latitude), 7),
        "origin_longitude": round(float(origin_longitude), 7),
        "center_latitude": round(float(center_latitude), 7) if center_latitude is not None else None,
        "center_longitude": round(float(center_longitude), 7) if center_longitude is not None else None,
        "center_name": center_name,
        "version": ROUTING_CACHE_VERSION,
    }

    digest = hashlib.md5(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return settings.cache_dir / "routing" / f"{digest}.json"


def _load_cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Не удалось прочитать кеш routing %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        return None

    logger.info("Routing загружен из кеша: %s", path)
    return data


def _save_cached(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Routing сохранён в кеш: %s", path)


def _haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    radius_km = 6371.0

    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )

    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fallback_metrics(
    origin_latitude: float,
    origin_longitude: float,
    center_latitude: float | None,
    center_longitude: float | None,
    center_name: str | None,
    center_city: str | None,
    center_source: str | None,
    data_source: str,
    error: str | None = None,
) -> dict[str, Any]:
    if center_latitude is None or center_longitude is None:
        return {
            "drive_time_min": None,
            "drive_distance_km": None,
            "walk_time_min": None,
            "walk_distance_km": None,
            "center_name": center_name,
            "center_city": center_city,
            "center_latitude": center_latitude,
            "center_longitude": center_longitude,
            "center_source": center_source,
            "data_source": "fallback_no_center",
            "error": error,
        }

    straight_km = _haversine_km(
        origin_latitude,
        origin_longitude,
        center_latitude,
        center_longitude,
    )

    # Прокси: реальный автомобильный путь обычно длиннее прямой линии.
    drive_distance_km = round(straight_km * 1.35, 2)

    # Средняя городская скорость с перекрёстками и светофорами.
    drive_time_min = round((drive_distance_km / 28.0) * 60.0, 1)

    walk_distance_km = round(straight_km * 1.2, 2)
    walk_time_min = round((walk_distance_km / 4.5) * 60.0, 1)

    return {
        "drive_time_min": drive_time_min,
        "drive_distance_km": drive_distance_km,
        "walk_time_min": walk_time_min,
        "walk_distance_km": walk_distance_km,
        "center_name": center_name,
        "center_city": center_city,
        "center_latitude": center_latitude,
        "center_longitude": center_longitude,
        "center_source": center_source,
        "data_source": data_source,
        "error": error,
    }


def _extract_route_metrics(data: dict[str, Any]) -> tuple[float | None, float | None]:
    """Возвращает время в минутах и дистанцию в км из ответа 2GIS Routing.

    Поддерживает несколько вариантов структуры ответа, чтобы не ломаться
    при небольших отличиях формата API.
    """
    result = data.get("result")

    candidates: list[dict[str, Any]] = []

    if isinstance(result, dict):
        if isinstance(result.get("routes"), list):
            candidates.extend(item for item in result["routes"] if isinstance(item, dict))
        if isinstance(result.get("route"), dict):
            candidates.append(result["route"])

    if isinstance(data.get("routes"), list):
        candidates.extend(item for item in data["routes"] if isinstance(item, dict))

    for route in candidates:
        duration_sec = (
            route.get("duration")
            or route.get("duration_s")
            or route.get("total_duration")
            or route.get("time")
        )

        distance_m = (
            route.get("distance")
            or route.get("distance_m")
            or route.get("total_distance")
            or route.get("length")
        )

        try:
            duration_min = round(float(duration_sec) / 60.0, 1) if duration_sec is not None else None
        except (TypeError, ValueError):
            duration_min = None

        try:
            distance_km = round(float(distance_m) / 1000.0, 2) if distance_m is not None else None
        except (TypeError, ValueError):
            distance_km = None

        if duration_min is not None or distance_km is not None:
            return duration_min, distance_km

    return None, None


def _request_route(
    origin_latitude: float,
    origin_longitude: float,
    center_latitude: float,
    center_longitude: float,
) -> dict[str, Any]:
    settings = get_settings()

    url = f"{settings.dgis_routing_url.rstrip('/')}/routing/7.0.0/global"

    payload = {
        "points": [
            {
                "type": "walking",
                "lat": float(origin_latitude),
                "lon": float(origin_longitude),
            },
            {
                "type": "walking",
                "lat": float(center_latitude),
                "lon": float(center_longitude),
            },
        ],
        "transport": "car",
        "route_mode": "fastest",
        "output": "summary",
    }

    response = requests.post(
        url,
        params={"key": settings.dgis_api_key},
        json=payload,
        timeout=settings.dgis_timeout,
    )

    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        return {}

    response.raise_for_status()
    return data


def get_drive_metrics(
    origin_latitude: float,
    origin_longitude: float,
    *,
    center_latitude: float | None,
    center_longitude: float | None,
    center_name: str | None = None,
    center_city: str | None = None,
    center_source: str | None = None,
) -> dict[str, Any]:
    """Считает автомобильную доступность до центра города.

    Если центра нет или API не отвечает, возвращает fallback с явным data_source.
    """
    settings = get_settings()

    cache_path = _cache_path(
        origin_latitude,
        origin_longitude,
        center_latitude,
        center_longitude,
        center_name,
    )

    if settings.use_cache and not settings.refresh_cache:
        cached = _load_cached(cache_path)
        if cached is not None:
            return cached

    if center_latitude is None or center_longitude is None:
        metrics = _fallback_metrics(
            origin_latitude,
            origin_longitude,
            center_latitude,
            center_longitude,
            center_name,
            center_city,
            center_source,
            data_source="fallback_no_center",
        )

        if settings.use_cache:
            _save_cached(cache_path, metrics)

        return metrics

    if settings.no_api:
        metrics = _fallback_metrics(
            origin_latitude,
            origin_longitude,
            center_latitude,
            center_longitude,
            center_name,
            center_city,
            center_source,
            data_source="fallback_no_api",
        )

        if settings.use_cache:
            _save_cached(cache_path, metrics)

        return metrics

    try:
        raw_data = _request_route(
            origin_latitude,
            origin_longitude,
            float(center_latitude),
            float(center_longitude),
        )

        drive_time_min, drive_distance_km = _extract_route_metrics(raw_data)

        if drive_time_min is None and drive_distance_km is None:
            metrics = _fallback_metrics(
                origin_latitude,
                origin_longitude,
                center_latitude,
                center_longitude,
                center_name,
                center_city,
                center_source,
                data_source="fallback_empty_route",
                error=json.dumps(raw_data, ensure_ascii=False)[:800],
            )
        else:
            fallback_walk = _fallback_metrics(
                origin_latitude,
                origin_longitude,
                center_latitude,
                center_longitude,
                center_name,
                center_city,
                center_source,
                data_source="fallback_walk_proxy",
            )

            metrics = {
                "drive_time_min": drive_time_min,
                "drive_distance_km": drive_distance_km,
                "walk_time_min": fallback_walk.get("walk_time_min"),
                "walk_distance_km": fallback_walk.get("walk_distance_km"),
                "center_name": center_name,
                "center_city": center_city,
                "center_latitude": center_latitude,
                "center_longitude": center_longitude,
                "center_source": center_source,
                "data_source": "2gis_routing_api",
                "error": None,
            }

    except Exception as exc:
        logger.warning("2GIS Routing API error: %s", exc)

        metrics = _fallback_metrics(
            origin_latitude,
            origin_longitude,
            center_latitude,
            center_longitude,
            center_name,
            center_city,
            center_source,
            data_source="fallback_routing_error",
            error=str(exc),
        )

    if settings.use_cache:
        _save_cached(cache_path, metrics)

    return metrics