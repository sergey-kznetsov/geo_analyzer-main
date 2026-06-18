from __future__ import annotations

"""Small diagnostics for checking the active 2GIS API key.

The diagnostic is intentionally separate from the report pipeline. It helps to
see which key source is active and which 2GIS endpoints are available before
running the full analysis.
"""

import json
import os
from pathlib import Path
from typing import Any

import requests

from geo_analyzer.core.settings import get_settings

DEFAULT_CHECK_LATITUDE = 56.853003
DEFAULT_CHECK_LONGITUDE = 53.199365
DEFAULT_CHECK_RUBRIC_ID = "350"


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "<empty>"
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def _parse_env_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "DGIS_API_KEY":
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def active_key_info() -> dict[str, Any]:
    settings = get_settings()
    env_key = os.getenv("DGIS_API_KEY", "").strip()
    app_env_key = _parse_env_file(settings.app_dir / ".env")
    project_env_key = _parse_env_file(settings.project_root / ".env")
    active_key = settings.dgis_api_key

    if env_key:
        source = "environment:DGIS_API_KEY"
    elif app_env_key:
        source = f"app_env:{settings.app_dir / '.env'}"
    elif project_env_key:
        source = f"project_env:{settings.project_root / '.env'}"
    elif active_key:
        source = "embedded_secret"
    else:
        source = "missing"

    return {
        "source": source,
        "active_key_masked": _mask_secret(active_key),
        "has_active_key": bool(active_key),
        "environment_key_masked": _mask_secret(env_key) if env_key else "",
        "app_env_key_masked": _mask_secret(app_env_key) if app_env_key else "",
        "project_env_key_masked": _mask_secret(project_env_key) if project_env_key else "",
        "note": "Environment variable DGIS_API_KEY has priority over .env. Restart PowerShell or clear $env:DGIS_API_KEY if .env was changed.",
    }


def _meta_error(payload: Any, status_code: int | None = None) -> tuple[int | None, str, str]:
    if not isinstance(payload, dict):
        return status_code, "", ""
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    try:
        code = int(meta.get("code")) if meta.get("code") is not None else status_code
    except (TypeError, ValueError):
        code = status_code
    error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    return code, str(error.get("type") or ""), str(error.get("message") or "")


def _call_json(name: str, url: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    safe_params = {key: value for key, value in params.items() if key != "key"}
    try:
        response = requests.get(url, params=params, timeout=timeout)
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"raw_text": response.text[:1000]}
        code, error_type, message = _meta_error(payload, status_code=response.status_code)
        ok = response.ok and (code is None or code < 400)
        return {
            "name": name,
            "ok": bool(ok),
            "http_status": response.status_code,
            "meta_code": code,
            "error_type": error_type,
            "message": message,
            "request_params": safe_params,
        }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "http_status": None,
            "meta_code": None,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "request_params": safe_params,
        }


def check_dgis_key(*, latitude: float = DEFAULT_CHECK_LATITUDE, longitude: float = DEFAULT_CHECK_LONGITUDE) -> dict[str, Any]:
    settings = get_settings()
    info = active_key_info()
    key = settings.dgis_api_key
    result: dict[str, Any] = {
        "key": info,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "catalog_url": settings.dgis_catalog_url,
        "checks": [],
    }

    if not key:
        result["ok"] = False
        result["summary"] = "DGIS_API_KEY is missing."
        return result

    region_url = f"{settings.dgis_catalog_url.rstrip('/')}/2.0/region/get"
    region_check = _call_json(
        "region/get",
        region_url,
        {"key": key, "point": f"{float(longitude)},{float(latitude)}"},
        settings.dgis_timeout,
    )
    result["checks"].append(region_check)

    region_id = ""
    if region_check["ok"]:
        try:
            payload = requests.get(
                region_url,
                params={"key": key, "point": f"{float(longitude)},{float(latitude)}"},
                timeout=settings.dgis_timeout,
            ).json()
            data = payload.get("result") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                region_id = str(data.get("id") or "").strip()
            items = data.get("items") if isinstance(data, dict) else None
            if not region_id and isinstance(items, list) and items:
                region_id = str(items[0].get("id") or "").strip()
        except Exception:
            region_id = ""
    if not region_id:
        region_id = settings.dgis_region_id or "41"

    catalog_url = f"{settings.dgis_catalog_url.rstrip('/')}/2.0/catalog/rubric/list"
    result["checks"].append(
        _call_json(
            "catalog/rubric/list",
            catalog_url,
            {"key": key, "region_id": region_id, "locale": "ru_RU", "page": 1, "page_size": 1},
            settings.dgis_timeout,
        )
    )

    places_url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    result["checks"].append(
        _call_json(
            "places/items",
            places_url,
            {
                "key": key,
                "region_id": region_id,
                "rubric_id": DEFAULT_CHECK_RUBRIC_ID,
                "point": f"{float(longitude)},{float(latitude)}",
                "location": f"{float(longitude)},{float(latitude)}",
                "radius": 1000,
                "page": 1,
                "page_size": 1,
                "fields": "items.id,items.name,items.point,items.rubrics",
            },
            settings.dgis_timeout,
        )
    )

    result["region_id"] = region_id
    result["ok"] = all(bool(check.get("ok")) for check in result["checks"])
    if result["ok"]:
        result["summary"] = "2GIS key is valid for region, catalog and Places API checks."
    else:
        failed = [check for check in result["checks"] if not check.get("ok")]
        result["summary"] = "; ".join(
            f"{check.get('name')}: code={check.get('meta_code') or check.get('http_status')}, type={check.get('error_type')}, message={check.get('message')}"
            for check in failed
        )
    return result


def format_dgis_key_check(result: dict[str, Any]) -> str:
    lines = [
        "2GIS API diagnostics",
        f"Active key: {result.get('key', {}).get('active_key_masked')} ({result.get('key', {}).get('source')})",
        f"Point: {result.get('latitude')}, {result.get('longitude')}",
        f"Region ID: {result.get('region_id', '')}",
        "",
    ]
    for check in result.get("checks", []):
        status = "OK" if check.get("ok") else "FAIL"
        lines.append(
            f"[{status}] {check.get('name')}: http={check.get('http_status')} meta={check.get('meta_code')} "
            f"type={check.get('error_type')} message={check.get('message')}"
        )
    lines.extend(["", str(result.get("summary") or "")])
    note = result.get("key", {}).get("note")
    if note:
        lines.extend(["", note])
    return "\n".join(lines)


__all__ = ["active_key_info", "check_dgis_key", "format_dgis_key_check"]
