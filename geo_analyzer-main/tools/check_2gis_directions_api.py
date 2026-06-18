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


INTERESTING_KEYWORDS = [
    "traffic",
    "jam",
    "jams",
    "congestion",
    "speed",
    "current_speed",
    "freeflow",
    "free_flow",
    "freeFlow",
    "duration",
    "distance",
    "time",
    "travel_time",
    "delay",
    "road",
    "route",
    "length",
    "density",
    "load",
]


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _json_preview(data: Any, limit: int = 2500) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        text = str(data)

    if len(text) > limit:
        return text[:limit] + "\n... TRUNCATED ..."

    return text


def _find_interesting_fields(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []

    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            lowered_key = str(key).lower()

            if any(word.lower() in lowered_key for word in INTERESTING_KEYWORDS):
                if not isinstance(value, (dict, list)):
                    found.append((path, value))

            found.extend(_find_interesting_fields(value, path))

    elif isinstance(data, list):
        for index, item in enumerate(data):
            path = f"{prefix}[{index}]"
            found.extend(_find_interesting_fields(item, path))

    return found


def _collect_route_like_objects(data: Any, prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    routes: list[tuple[str, dict[str, Any]]] = []

    if isinstance(data, dict):
        keys = set(data.keys())
        score = 0

        for key in [
            "duration",
            "distance",
            "total_duration",
            "total_distance",
            "travel_time",
            "route",
            "geometry",
            "maneuvers",
        ]:
            if key in keys:
                score += 1

        if score >= 1 and any(key in keys for key in ["duration", "distance", "total_duration", "total_distance", "travel_time"]):
            routes.append((prefix or "root", data))

        for key, value in data.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            routes.extend(_collect_route_like_objects(value, child_prefix))

    elif isinstance(data, list):
        for index, item in enumerate(data):
            child_prefix = f"{prefix}[{index}]"
            routes.extend(_collect_route_like_objects(item, child_prefix))

    return routes


def _extract_first_number(route: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = _safe_float(route.get(key))
        if value is not None:
            return value

    return None


def _extract_metrics(route: dict[str, Any]) -> dict[str, Any]:
    duration_sec = _extract_first_number(
        route,
        [
            "duration",
            "duration_s",
            "duration_sec",
            "total_duration",
            "travel_time",
            "travel_time_sec",
            "time",
        ],
    )

    distance_m = _extract_first_number(
        route,
        [
            "distance",
            "distance_m",
            "total_distance",
            "length",
            "route_length",
        ],
    )

    current_speed = _extract_first_number(
        route,
        [
            "currentSpeed",
            "current_speed",
            "speed",
            "average_speed",
            "avg_speed",
        ],
    )

    freeflow_speed = _extract_first_number(
        route,
        [
            "freeFlowSpeed",
            "freeflow_speed",
            "free_flow_speed",
            "freeSpeed",
        ],
    )

    current_travel_time = _extract_first_number(
        route,
        [
            "currentTravelTime",
            "current_travel_time",
            "travelTime",
        ],
    )

    freeflow_travel_time = _extract_first_number(
        route,
        [
            "freeFlowTravelTime",
            "freeflow_travel_time",
            "free_flow_travel_time",
            "freeTravelTime",
        ],
    )

    duration_min = round(duration_sec / 60, 2) if duration_sec is not None else None
    distance_km = round(distance_m / 1000, 3) if distance_m is not None else None

    avg_speed_kmh = None
    if duration_min is not None and distance_km is not None and duration_min > 0:
        avg_speed_kmh = round(distance_km / (duration_min / 60), 2)

    traffic_load_index = None
    traffic_quality_score = None
    traffic_source = None

    if current_speed is not None and freeflow_speed is not None and freeflow_speed > 0:
        load_ratio = max(0.0, min(1.0, 1.0 - current_speed / freeflow_speed))
        traffic_load_index = round(load_ratio * 10, 2)
        traffic_quality_score = round(10 - traffic_load_index, 2)
        traffic_source = "currentSpeed/freeFlowSpeed"

    elif current_travel_time is not None and freeflow_travel_time is not None and freeflow_travel_time > 0:
        delay_ratio = max(0.0, current_travel_time / freeflow_travel_time - 1.0)
        load_ratio = min(1.0, delay_ratio)
        traffic_load_index = round(load_ratio * 10, 2)
        traffic_quality_score = round(10 - traffic_load_index, 2)
        traffic_source = "currentTravelTime/freeFlowTravelTime"

    return {
        "duration_sec": duration_sec,
        "duration_min": duration_min,
        "distance_m": distance_m,
        "distance_km": distance_km,
        "avg_speed_kmh_by_route": avg_speed_kmh,
        "current_speed": current_speed,
        "freeflow_speed": freeflow_speed,
        "current_travel_time": current_travel_time,
        "freeflow_travel_time": freeflow_travel_time,
        "traffic_load_index_0_10": traffic_load_index,
        "traffic_quality_score_0_10": traffic_quality_score,
        "traffic_source": traffic_source,
    }


def _traffic_class(load_index: float | None) -> str:
    if load_index is None:
        return "нет прямых traffic-данных"

    if load_index <= 2:
        return "свободно"
    if load_index <= 4:
        return "низкая загрузка"
    if load_index <= 6:
        return "умеренная загрузка"
    if load_index <= 8:
        return "высокая загрузка"

    return "сильная загрузка"


def _base_urls(settings: Any) -> list[str]:
    urls: list[str] = []

    for attr in [
        "dgis_directions_url",
        "directions_url",
        "dgis_routing_url",
        "routing_url",
    ]:
        value = getattr(settings, attr, None)
        if value:
            urls.append(str(value).rstrip("/"))

    urls.extend(
        [
            "https://routing.api.2gis.com",
            "https://directions.api.2gis.com",
        ]
    )

    unique: list[str] = []
    for url in urls:
        if url and url not in unique:
            unique.append(url)

    return unique


def _endpoint_candidates(base_url: str) -> list[str]:
    base_url = base_url.rstrip("/")

    return [
        f"{base_url}/routing/7.0.0/global",
        f"{base_url}/directions/7.0.0/global",
        f"{base_url}/directions/6.0.0/global",
        f"{base_url}/carrouting/6.0.0/global",
        f"{base_url}/get_car_route",
        f"{base_url}/route",
    ]


def _payload_candidates(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> list[dict[str, Any]]:
    return [
        {
            "name": "points_walking_fastest_summary",
            "body": {
                "points": [
                    {
                        "type": "walking",
                        "lat": origin_lat,
                        "lon": origin_lon,
                    },
                    {
                        "type": "walking",
                        "lat": dest_lat,
                        "lon": dest_lon,
                    },
                ],
                "transport": "car",
                "route_mode": "fastest",
                "output": "summary",
            },
        },
        {
            "name": "points_stop_fastest_summary",
            "body": {
                "points": [
                    {
                        "type": "stop",
                        "lat": origin_lat,
                        "lon": origin_lon,
                    },
                    {
                        "type": "stop",
                        "lat": dest_lat,
                        "lon": dest_lon,
                    },
                ],
                "transport": "car",
                "route_mode": "fastest",
                "output": "summary",
            },
        },
        {
            "name": "points_walking_traffic_summary",
            "body": {
                "points": [
                    {
                        "type": "walking",
                        "lat": origin_lat,
                        "lon": origin_lon,
                    },
                    {
                        "type": "walking",
                        "lat": dest_lat,
                        "lon": dest_lon,
                    },
                ],
                "transport": "car",
                "route_mode": "fastest",
                "traffic_mode": "jam",
                "output": "summary",
            },
        },
        {
            "name": "lonlat_array_fastest",
            "body": {
                "points": [
                    [origin_lon, origin_lat],
                    [dest_lon, dest_lat],
                ],
                "transport": "car",
                "route_mode": "fastest",
                "output": "summary",
            },
        },
    ]


def _send_post(url: str, api_key: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = requests.post(
        url,
        params={"key": api_key},
        json=body,
        timeout=timeout,
    )

    try:
        response_json = response.json()
    except Exception:
        response_json = {
            "text": response.text,
        }

    return {
        "method": "POST",
        "url": url,
        "status_code": response.status_code,
        "request_body": body,
        "response": response_json,
    }


def _send_get(url: str, api_key: str, origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, timeout: int) -> dict[str, Any]:
    params = {
        "key": api_key,
        "from": f"{origin_lon},{origin_lat}",
        "to": f"{dest_lon},{dest_lat}",
        "transport": "car",
        "route_mode": "fastest",
        "output": "summary",
    }

    response = requests.get(
        url,
        params=params,
        timeout=timeout,
    )

    try:
        response_json = response.json()
    except Exception:
        response_json = {
            "text": response.text,
        }

    return {
        "method": "GET",
        "url": url,
        "status_code": response.status_code,
        "request_params": params,
        "response": response_json,
    }


def _print_analysis(result: dict[str, Any]) -> bool:
    response = result.get("response")
    status_code = result.get("status_code")

    print(f"HTTP {status_code} | {result.get('method')} {result.get('url')}")

    if status_code is None or int(status_code) >= 400:
        print("ERROR RESPONSE:")
        print(_json_preview(response, limit=1200))
        return False

    route_objects = _collect_route_like_objects(response)
    interesting = _find_interesting_fields(response)

    print(f"Route-like objects found: {len(route_objects)}")

    success_with_traffic = False

    for index, (path, route) in enumerate(route_objects[:5], start=1):
        metrics = _extract_metrics(route)
        traffic_class = _traffic_class(metrics["traffic_load_index_0_10"])

        print("")
        print(f"Route object #{index}: {path}")
        print(f"Duration, sec: {metrics['duration_sec']}")
        print(f"Duration, min: {metrics['duration_min']}")
        print(f"Distance, m: {metrics['distance_m']}")
        print(f"Distance, km: {metrics['distance_km']}")
        print(f"Avg speed by route, km/h: {metrics['avg_speed_kmh_by_route']}")
        print(f"Current speed: {metrics['current_speed']}")
        print(f"Freeflow speed: {metrics['freeflow_speed']}")
        print(f"Current travel time: {metrics['current_travel_time']}")
        print(f"Freeflow travel time: {metrics['freeflow_travel_time']}")
        print(f"Traffic load index, 0-10: {metrics['traffic_load_index_0_10']}")
        print(f"Traffic quality score, 0-10: {metrics['traffic_quality_score_0_10']}")
        print(f"Traffic class: {traffic_class}")
        print(f"Traffic source: {metrics['traffic_source']}")

        if metrics["traffic_load_index_0_10"] is not None:
            success_with_traffic = True

    print("")
    print("Interesting fields:")

    if not interesting:
        print("No traffic-like fields found")
    else:
        for path, value in interesting[:120]:
            print(f"{path}: {value}")

    return success_with_traffic


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument("--origin-lat", type=float, default=56.866315)
    parser.add_argument("--origin-lon", type=float, default=53.207313)
    parser.add_argument("--dest-lat", type=float, default=56.8526)
    parser.add_argument("--dest-lon", type=float, default=53.2115)
    parser.add_argument("--all", action="store_true", help="Try all endpoint/payload combinations")
    parser.add_argument("--timeout", type=int, default=20)

    args = parser.parse_args()

    settings = get_settings()

    if not settings.dgis_api_key:
        raise RuntimeError("2GIS API key is empty")

    debug_dir = ROOT / "data" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("2GIS Directions API diagnostics")
    print(f"Origin: {args.origin_lat}, {args.origin_lon}")
    print(f"Destination: {args.dest_lat}, {args.dest_lon}")
    print("")

    payloads = _payload_candidates(
        origin_lat=args.origin_lat,
        origin_lon=args.origin_lon,
        dest_lat=args.dest_lat,
        dest_lon=args.dest_lon,
    )

    tested = 0
    traffic_found = False

    for base_url in _base_urls(settings):
        for endpoint in _endpoint_candidates(base_url):
            for payload in payloads:
                tested += 1

                print("=" * 100)
                print(f"TEST #{tested}")
                print(f"Endpoint: {endpoint}")
                print(f"Payload: {payload['name']}")

                try:
                    result = _send_post(
                        url=endpoint,
                        api_key=settings.dgis_api_key,
                        body=payload["body"],
                        timeout=args.timeout,
                    )
                except Exception as exc:
                    print(f"REQUEST ERROR: {exc}")
                    continue

                safe_endpoint_name = (
                    endpoint.replace("https://", "")
                    .replace("http://", "")
                    .replace("/", "_")
                    .replace(".", "_")
                    .replace(":", "_")
                )

                raw_path = debug_dir / f"directions_api_{timestamp}_{tested}_{safe_endpoint_name}_{payload['name']}.json"
                raw_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                print(f"Raw response saved: {raw_path}")

                if _print_analysis(result):
                    traffic_found = True

                if not args.all and result.get("status_code") == 200:
                    print("")
                    print("First working endpoint found. Use --all to continue testing all variants.")
                    print("")
                    if traffic_found:
                        print("RESULT: direct traffic fields found.")
                    else:
                        print("RESULT: endpoint works, but direct traffic fields were not found in parsed response.")
                    return 0

    print("")
    print("=" * 100)
    print("FINAL RESULT")

    if traffic_found:
        print("Direct traffic fields were found. We can build traffic load index from API fields.")
    else:
        print("Direct traffic fields were not found in tested responses.")
        print("If this key supports visual traffic layer only, it is not enough for numeric report index.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())