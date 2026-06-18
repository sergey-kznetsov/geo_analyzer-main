from __future__ import annotations

import os
from typing import Any

import requests

from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.diagnostics import active_key_info
from geo_analyzer.ingestion.dgis.geocoder import DGISGeocoder
from geo_analyzer.ingestion.dgis.region_runtime_patch import ENV_REGION_ID, ENV_REGION_NAME, get_region_for_point

DEFAULT_CHECK_LATITUDE = 56.853003
DEFAULT_CHECK_LONGITUDE = 53.199365
DEFAULT_CHECK_RUBRIC_ID = "350"


def _meta(payload: Any, http_status: int | None = None) -> tuple[int | None, str, str]:
    if not isinstance(payload, dict):
        return http_status, "", ""
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    try:
        code = int(meta.get("code")) if meta.get("code") is not None else http_status
    except (TypeError, ValueError):
        code = http_status
    return code, str(error.get("type") or ""), str(error.get("message") or "")


def _is_empty_places_result(code: int | None, error_type: str, message: str) -> bool:
    normalized_type = (error_type or "").strip().lower()
    normalized_message = (message or "").strip().lower()
    return code == 404 and (
        normalized_type == "itemnotfound"
        or "results not found" in normalized_message
        or "not found" in normalized_message
    )


def _call(
    name: str,
    url: str,
    params: dict[str, Any],
    timeout: int,
    *,
    empty_result_is_ok: bool = False,
) -> dict[str, Any]:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        try:
            data: Any = response.json()
        except ValueError:
            data = {}
        code, error_type, message = _meta(data, response.status_code)
        empty_result = bool(empty_result_is_ok and _is_empty_places_result(code, error_type, message))
        ok = response.ok and (code is None or code < 400 or empty_result)
        if empty_result:
            message = "API reachable; sample query returned no objects for this rubric/radius."
        return {
            "name": name,
            "ok": ok,
            "empty_result": empty_result,
            "http_status": response.status_code,
            "meta_code": code,
            "error_type": error_type,
            "message": message,
        }
    except Exception as exc:
        return {"name": name, "ok": False, "empty_result": False, "http_status": None, "meta_code": None, "error_type": type(exc).__name__, "message": str(exc)}


def _resolve_region(*, address: str | None, latitude: float, longitude: float) -> tuple[str, str, dict[str, Any]]:
    previous_id = os.getenv(ENV_REGION_ID)
    previous_name = os.getenv(ENV_REGION_NAME)
    try:
        if address:
            loc = DGISGeocoder().geocode(address)
            if loc.region_id:
                os.environ[ENV_REGION_ID] = str(loc.region_id)
                os.environ[ENV_REGION_NAME] = str(loc.region_name or "")
                return str(loc.region_id), str(loc.region_name or ""), {
                    "name": "region/resolve",
                    "ok": True,
                    "http_status": 200,
                    "meta_code": 200,
                    "empty_result": False,
                    "error_type": "",
                    "message": f"geocoder items.region_id; {loc.resolved_address or address}",
                }
            latitude = loc.latitude
            longitude = loc.longitude

        region = get_region_for_point(latitude, longitude)
        return str(region.id or ""), str(region.name or ""), {
            "name": "region/resolve",
            "ok": bool(region.id),
            "http_status": 200 if region.id else None,
            "meta_code": 200 if region.id else None,
            "empty_result": False,
            "error_type": "" if region.id else "regionNotResolved",
            "message": "runtime/coordinate region resolver",
        }
    except Exception as exc:
        return "", "", {"name": "region/resolve", "ok": False, "empty_result": False, "http_status": None, "meta_code": None, "error_type": type(exc).__name__, "message": str(exc)}
    finally:
        if previous_id is None:
            os.environ.pop(ENV_REGION_ID, None)
        else:
            os.environ[ENV_REGION_ID] = previous_id
        if previous_name is None:
            os.environ.pop(ENV_REGION_NAME, None)
        else:
            os.environ[ENV_REGION_NAME] = previous_name


def check_dgis_key(*, latitude: float = DEFAULT_CHECK_LATITUDE, longitude: float = DEFAULT_CHECK_LONGITUDE, address: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    info = active_key_info()
    result: dict[str, Any] = {"key": info, "address": address or "", "latitude": latitude, "longitude": longitude, "checks": []}
    if not settings.dgis_api_key:
        result["ok"] = False
        result["summary"] = "DGIS_API_KEY is missing."
        return result

    region_id, region_name, region_check = _resolve_region(address=address, latitude=float(latitude), longitude=float(longitude))
    result["checks"].append(region_check)
    if not region_id:
        region_id = settings.dgis_region_id or "41"
        region_name = "config_fallback_after_region_check"

    base = settings.dgis_catalog_url.rstrip("/")
    result["checks"].append(
        _call(
            "catalog/rubric/list",
            f"{base}/2.0/catalog/rubric/list",
            {"key": settings.dgis_api_key, "region_id": region_id, "locale": "ru_RU", "page": 1, "page_size": 1},
            settings.dgis_timeout,
        )
    )
    result["checks"].append(
        _call(
            "places/items",
            f"{base}/3.0/items",
            {
                "key": settings.dgis_api_key,
                "region_id": region_id,
                "rubric_id": DEFAULT_CHECK_RUBRIC_ID,
                "location": f"{float(longitude)},{float(latitude)}",
                "point": f"{float(longitude)},{float(latitude)}",
                "radius": 1000,
                "page": 1,
                "page_size": 1,
                "fields": "items.id,items.name,items.point,items.rubrics,items.region_id",
            },
            settings.dgis_timeout,
            empty_result_is_ok=True,
        )
    )

    result["region_id"] = region_id
    result["region_name"] = region_name
    result["ok"] = all(bool(check.get("ok")) for check in result["checks"])
    failures = [check for check in result["checks"] if not check.get("ok")]
    empty_checks = [check for check in result["checks"] if check.get("empty_result")]
    if failures:
        result["summary"] = "; ".join(
            f"{c.get('name')}: code={c.get('meta_code') or c.get('http_status')}, type={c.get('error_type')}, message={c.get('message')}"
            for c in failures
        )
    elif empty_checks:
        result["summary"] = "2GIS diagnostics passed. Some sample Places queries returned no objects, but the API endpoint is reachable."
    else:
        result["summary"] = "2GIS diagnostics passed."
    return result


def format_dgis_key_check(result: dict[str, Any]) -> str:
    lines = [
        "2GIS API diagnostics",
        f"Active key: {result.get('key', {}).get('active_key_masked')} ({result.get('key', {}).get('source')})",
        f"Address: {result.get('address', '')}",
        f"Point: {result.get('latitude')}, {result.get('longitude')}",
        f"Region ID: {result.get('region_id', '')}",
        f"Region name: {result.get('region_name', '')}",
        "",
    ]
    for check in result.get("checks", []):
        status = "EMPTY" if check.get("empty_result") else ("OK" if check.get("ok") else "FAIL")
        lines.append(f"[{status}] {check.get('name')}: http={check.get('http_status')} meta={check.get('meta_code')} type={check.get('error_type')} message={check.get('message')}")
    lines.append("")
    lines.append(str(result.get("summary") or ""))
    note = result.get("key", {}).get("note")
    if note:
        lines.extend(["", note])
    return "\n".join(lines)


__all__ = ["check_dgis_key", "format_dgis_key_check"]