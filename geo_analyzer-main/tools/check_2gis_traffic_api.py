from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from geo_analyzer.core.settings import get_settings


TRAFFIC_KEYWORDS = [
    "traffic",
    "jam",
    "jams",
    "congestion",
    "speed",
    "duration",
    "distance",
    "time",
    "delay",
    "road",
    "route",
]


def _find_interesting_fields(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    result: list[tuple[str, Any]] = []

    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()

            if any(word in lowered for word in TRAFFIC_KEYWORDS):
                if not isinstance(value, (dict, list)):
                    result.append((path, value))

            result.extend(_find_interesting_fields(value, path))

    elif isinstance(data, list):
        for index, item in enumerate(data):
            path = f"{prefix}[{index}]"
            result.extend(_find_interesting_fields(item, path))

    return result


def _extract_route_candidates(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    result = data.get("result")

    if isinstance(result, dict):
        routes = result.get("routes")
        route = result.get("route")

        if isinstance(routes, list):
            candidates.extend(item for item in routes if isinstance(item, dict))

        if isinstance(route, dict):
            candidates.append(route)

    routes = data.get("routes")
    route = data.get("route")

    if isinstance(routes, list):
        candidates.extend(item for item in routes if isinstance(item, dict))

    if isinstance(route, dict):
        candidates.append(route)

    return candidates


def _extract_duration_distance(route: dict[str, Any]) -> dict[str, Any]:
    duration_sec = (
        route.get("duration")
        or route.get("duration_s")
        or route.get("total_duration")
        or route.get("time")
        or route.get("travel_time")
    )

    distance_m = (
        route.get("distance")
        or route.get("distance_m")
        or route.get("total_distance")
        or route.get("length")
    )

    duration_min = None
    distance_km = None
    speed_kmh = None

    try:
        if duration_sec is not None:
            duration_min = round(float(duration_sec) / 60.0, 2)
    except Exception:
        pass

    try:
        if distance_m is not None:
            distance_km = round(float(distance_m) / 1000.0, 3)
    except Exception:
        pass

    if duration_min and distance_km and duration_min > 0:
        speed_kmh = round(distance_km / (duration_min / 60.0), 2)

    return {
        "duration_raw": duration_sec,
        "distance_raw": distance_m,
        "duration_min": duration_min,
        "distance_km": distance_km,
        "avg_speed_kmh": speed_kmh,
    }


def _road_freedom_score(avg_speed_kmh: float | None) -> tuple[float, str]:
    if avg_speed_kmh is None:
        return 0.0, "нет данных"

    if avg_speed_kmh >= 40:
        return 10.0, "свободные дороги"
    if avg_speed_kmh >= 30:
        return 8.0, "низкая загрузка"
    if avg_speed_kmh >= 20:
        return 6.0, "умеренная загрузка"
    if avg_speed_kmh >= 15:
        return 4.0, "средняя загрузка"
    if avg_speed_kmh >= 10:
        return 2.0, "высокая загрузка"

    return 1.0, "сильная загрузка"


def request_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    output: str,
) -> dict[str, Any]:
    settings = get_settings()

    if not settings.dgis_api_key:
        raise RuntimeError("2GIS API key is empty")

    url = f"{settings.dgis_routing_url.rstrip('/')}/routing/7.0.0/global"

    payload = {
        "points": [
            {
                "type": "walking",
                "lat": float(origin_lat),
                "lon": float(origin_lon),
            },
            {
                "type": "walking",
                "lat": float(dest_lat),
                "lon": float(dest_lon),
            },
        ],
        "transport": "car",
        "route_mode": "fastest",
        "output": output,
    }

    response = requests.post(
        url,
        params={"key": settings.dgis_api_key},
        json=payload,
        timeout=settings.dgis_timeout,
    )

    try:
        data = response.json()
    except Exception:
        response.raise_for_status()
        return {
            "status_code": response.status_code,
            "text": response.text,
        }

    return {
        "status_code": response.status_code,
        "url": url,
        "payload": payload,
        "response": data,
    }


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument("--origin-lat", type=float, default=56.866315)
    parser.add_argument("--origin-lon", type=float, default=53.207313)
    parser.add_argument("--dest-lat", type=float, default=56.8526)
    parser.add_argument("--dest-lon", type=float, default=53.2115)
    parser.add_argument(
        "--outputs",
        nargs="+",
        default=["summary", "full"],
        help="Routing API output modes to test",
    )

    args = parser.parse_args()

    debug_dir = ROOT / "data" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("2GIS Routing API traffic diagnostics")
    print(f"Origin: {args.origin_lat}, {args.origin_lon}")
    print(f"Destination: {args.dest_lat}, {args.dest_lon}")
    print("")

    for output in args.outputs:
        print("=" * 80)
        print(f"Testing output={output}")

        try:
            result = request_route(
                origin_lat=args.origin_lat,
                origin_lon=args.origin_lon,
                dest_lat=args.dest_lat,
                dest_lon=args.dest_lon,
                output=output,
            )
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue

        path = debug_dir / f"traffic_api_{timestamp}_{output}.json"
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"HTTP status: {result.get('status_code')}")
        print(f"Raw response saved: {path}")

        response_data = result.get("response")

        if not isinstance(response_data, dict):
            print("Response is not JSON object")
            continue

        if result.get("status_code", 0) >= 400:
            print("API returned error:")
            print(json.dumps(response_data, ensure_ascii=False, indent=2)[:2000])
            continue

        routes = _extract_route_candidates(response_data)

        print(f"Route candidates found: {len(routes)}")

        for index, route in enumerate(routes[:3], start=1):
            metrics = _extract_duration_distance(route)
            score, road_class = _road_freedom_score(metrics["avg_speed_kmh"])

            print("")
            print(f"Route #{index}")
            print(f"Duration, min: {metrics['duration_min']}")
            print(f"Distance, km: {metrics['distance_km']}")
            print(f"Average speed, km/h: {metrics['avg_speed_kmh']}")
            print(f"Road freedom score, 0-10: {score}")
            print(f"Road load class: {road_class}")

        interesting = _find_interesting_fields(response_data)

        print("")
        print("Interesting traffic/speed/duration fields:")

        if not interesting:
            print("No traffic-specific fields found")
        else:
            for path_key, value in interesting[:80]:
                print(f"{path_key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())