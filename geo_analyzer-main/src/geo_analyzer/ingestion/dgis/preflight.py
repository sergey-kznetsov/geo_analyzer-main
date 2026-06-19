from __future__ import annotations

"""Preflight discovery for 2GIS regional categories and object fields.

The preflight step runs before the business report. It stores the complete
regional rubric catalog and a technical field/attribute survey for 2GIS objects
near the analysed location. The output is debug-only and is not exported to the
user Excel report.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.api_dictionary_logger import observe_object_dictionary
from geo_analyzer.ingestion.dgis.api_profile import observe_items, observe_response
from geo_analyzer.ingestion.dgis.category_catalog import (
    RegionRef,
    get_region_for_point,
    load_or_fetch_category_catalog,
    resolve_configured_place_rubrics,
)
from geo_analyzer.ingestion.dgis.places_enriched_loader import PLACE_DETAIL_FIELDS, STATION_FIELDS

logger = get_logger("geo_analyzer.dgis.preflight")

PREFLIGHT_VERSION = "dgis_preflight_v2_catalog_rubrics_and_typed_objects"
PREFLIGHT_SAMPLE_PAGE_SIZE = 5
PREFLIGHT_SLEEP_SEC = 0.08
TYPED_OBJECTS = {
    "station": STATION_FIELDS,
    "station_platform": STATION_FIELDS,
    "building": (
        PLACE_DETAIL_FIELDS
        + ",items.floors,items.floor_count,items.structure_info,items.structure_info.apartments_count,items.structure_info.porch_count,items.has_apartments_info,items.links.database_entrances.apartments_info,items.purpose_code"
    ),
}


class DGISAuthorizationError(RuntimeError):
    """Raised when 2GIS returns an authorization error that makes analysis invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe(value: Any) -> str:
    text = str(value or "unknown").strip() or "unknown"
    allowed = []
    for char in text:
        allowed.append(char if char.isalnum() or char in "._-" else "_")
    return "".join(allowed).strip("._")[:120] or "unknown"


def _debug_dir() -> Path:
    settings = get_settings()
    path = settings.logs_dir / "dgis_api_profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract_items(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return [item for item in result["items"] if isinstance(item, dict)]
    if isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def _meta_error(data: dict[str, Any] | None) -> tuple[int | None, str, str]:
    if not isinstance(data, dict):
        return None, "", ""
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    try:
        code = int(meta.get("code")) if meta.get("code") is not None else None
    except (TypeError, ValueError):
        code = None
    error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    message = str(error.get("message") or "")
    error_type = str(error.get("type") or "")
    return code, error_type, message


def _raise_if_blocked(data: dict[str, Any] | None, *, stage: str) -> None:
    code, error_type, message = _meta_error(data)
    haystack = f"{error_type} {message}".lower()
    if code == 403 and ("apikeyisblocked" in haystack or "key is blocked" in haystack):
        raise DGISAuthorizationError(
            "2GIS API key is blocked. "
            f"Stage={stage}. 2GIS response: {message or error_type}. "
            "Нужно заменить DGIS_API_KEY или обратиться в api@2gis.ru."
        )
    if code in {401, 403} and ("authorization" in haystack or "api" in haystack or "key" in haystack):
        raise DGISAuthorizationError(
            "2GIS API authorization failed. "
            f"Stage={stage}. code={code}; type={error_type}; message={message}. "
            "Проверь DGIS_API_KEY."
        )


def _catalog_paths(region_id: str) -> tuple[Path, Path]:
    base = _debug_dir()
    return (
        base / f"region_{_safe(region_id)}__preflight__rubric_catalog.json",
        base / f"region_{_safe(region_id)}__preflight__rubric_catalog.md",
    )


def _display_rubric(row: dict[str, Any]) -> str:
    return str(row.get("title") or row.get("caption") or row.get("name") or row.get("keyword") or row.get("alias") or row.get("id") or "").strip()


def _save_catalog_documents(region: RegionRef, catalog: dict[str, Any], *, latitude: float, longitude: float) -> dict[str, str]:
    items = [item for item in catalog.get("items", []) if isinstance(item, dict)]
    json_path, md_path = _catalog_paths(region.id)

    payload = {
        "generated_at": _now(),
        "version": PREFLIGHT_VERSION,
        "region_id": region.id,
        "region_name": region.name,
        "latitude": latitude,
        "longitude": longitude,
        "rubrics_count": len(items),
        "rubrics": items,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# 2GIS rubric catalog: region {region.id}",
        "",
        f"Generated: {payload['generated_at']}",
        f"Region: {region.name or region.id}",
        f"Rubrics: {len(items)}",
        "",
        "| rubric_id | parent_id | name | type | branch_count | org_count | geo_count |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in items:
        lines.append(
            "| {id} | {parent_id} | {name} | {type} | {branch_count} | {org_count} | {geo_count} |".format(
                id=str(row.get("id") or ""),
                parent_id=str(row.get("parent_id") or ""),
                name=_display_rubric(row).replace("|", "/"),
                type=str(row.get("type") or ""),
                branch_count=str(row.get("branch_count") or ""),
                org_count=str(row.get("org_count") or ""),
                geo_count=str(row.get("geo_count") or ""),
            )
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"catalog_json": str(json_path), "catalog_markdown": str(md_path)}


def _preflight_summary_path(region_id: str) -> Path:
    return _debug_dir() / f"region_{_safe(region_id)}__preflight__summary.json"


def _preflight_summary_md_path(region_id: str) -> Path:
    return _debug_dir() / f"region_{_safe(region_id)}__preflight__summary.md"


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if key != "key"}


def _request_items(params: dict[str, Any], *, stage: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    response = requests.get(url, params=params, timeout=settings.dgis_timeout)
    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        return [], {}

    _raise_if_blocked(data if isinstance(data, dict) else {}, stage=stage)
    if isinstance(data, dict):
        observe_response(
            region_id=str(params.get("region_id") or "unknown"),
            source="preflight_response",
            object_kind=stage,
            data=data,
            request_params=_clean_params(params),
        )
    code, error_type, message = _meta_error(data if isinstance(data, dict) else {})
    if code is not None and code >= 400:
        logger.warning("2GIS preflight sample skipped: stage=%s code=%s type=%s message=%s", stage, code, error_type, message)
        return [], data if isinstance(data, dict) else {}
    response.raise_for_status()
    return _extract_items(data if isinstance(data, dict) else {}), data if isinstance(data, dict) else {}


def _sample_rubric_objects(
    *,
    region_id: str,
    rubric_id: str,
    latitude: float,
    longitude: float,
    radius_m: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    params = {
        "key": settings.dgis_api_key,
        "rubric_id": str(rubric_id),
        "region_id": str(region_id),
        "point": f"{float(longitude)},{float(latitude)}",
        "location": f"{float(longitude)},{float(latitude)}",
        "radius": int(radius_m),
        "page": 1,
        "page_size": max(1, min(int(page_size), 10)),
        "fields": PLACE_DETAIL_FIELDS,
        "sort": "distance",
    }
    return _request_items(params, stage=f"preflight_rubric_{rubric_id}")


def _sample_typed_objects(
    *,
    region_id: str,
    object_type: str,
    fields: str,
    latitude: float,
    longitude: float,
    radius_m: int,
    page_size: int,
) -> list[dict[str, Any]]:
    settings = get_settings()
    params = {
        "key": settings.dgis_api_key,
        "region_id": str(region_id),
        "type": str(object_type),
        "point": f"{float(longitude)},{float(latitude)}",
        "location": f"{float(longitude)},{float(latitude)}",
        "radius": int(radius_m),
        "page": 1,
        "page_size": max(1, min(int(page_size), 10)),
        "fields": fields,
        "sort": "distance",
    }
    items, _raw = _request_items(params, stage=f"preflight_type_{object_type}")
    if items:
        observe_items(
            region_id=region_id,
            source="preflight_type_sample_items",
            object_kind=object_type,
            items=items,
            request_params={"type": object_type, "radius_m": int(radius_m), "page_size": page_size},
        )
        observe_object_dictionary(
            region_id=region_id,
            source="preflight_type_sample_items",
            object_kind=object_type,
            items=items,
            request_params={"type": object_type, "radius_m": int(radius_m), "page_size": page_size},
        )
    return items


def _preflight_scope_entries(catalog: dict[str, Any], configured_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings()
    scope = str(os.getenv("GEO_ANALYZER_DGIS_PREFLIGHT_SCOPE") or settings.config.get("dgis", {}).get("preflight_scope", "configured")).strip().lower()
    if scope == "all":
        entries: list[dict[str, Any]] = []
        for row in catalog.get("items", []):
            if not isinstance(row, dict):
                continue
            rubric_id = str(row.get("id") or "").strip()
            name = _display_rubric(row)
            if rubric_id and name:
                entries.append({"category": name, "rubric_name": name, "rubric_id": rubric_id})
        return entries
    return configured_entries


def _preflight_limit(default: int = 0) -> int:
    settings = get_settings()
    raw = os.getenv("GEO_ANALYZER_DGIS_PREFLIGHT_MAX_RUBRICS")
    if raw is None:
        raw = settings.config.get("dgis", {}).get("preflight_max_rubrics", default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(0, value)


def _save_summary(region: RegionRef, summary: dict[str, Any]) -> dict[str, str]:
    json_path = _preflight_summary_path(region.id)
    md_path = _preflight_summary_md_path(region.id)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    rows = summary.get("rubric_samples", []) if isinstance(summary.get("rubric_samples"), list) else []
    type_rows = summary.get("type_samples", []) if isinstance(summary.get("type_samples"), list) else []
    lines = [
        f"# 2GIS preflight summary: region {region.id}",
        "",
        f"Generated: {summary.get('generated_at')}",
        f"Rubrics in catalog: {summary.get('catalog_rubrics_count')}",
        f"Sampled rubrics: {summary.get('sampled_rubrics_count')}",
        f"Sampled rubric objects: {summary.get('sampled_objects_count')}",
        f"Sampled typed objects: {summary.get('sampled_typed_objects_count')}",
        "",
        "## Rubric samples",
        "| rubric_id | category | objects_sampled | status |",
        "|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {rubric_id} | {category} | {objects} | {status} |".format(
                rubric_id=str(row.get("rubric_id") or ""),
                category=str(row.get("category") or "").replace("|", "/"),
                objects=str(row.get("objects_sampled") or 0),
                status=str(row.get("status") or ""),
            )
        )
    lines.extend(["", "## Typed object samples", "| type | objects_sampled | status |", "|---|---:|---|"])
    for row in type_rows:
        lines.append(
            "| {object_type} | {objects} | {status} |".format(
                object_type=str(row.get("type") or ""),
                objects=str(row.get("objects_sampled") or 0),
                status=str(row.get("status") or ""),
            )
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"summary_json": str(json_path), "summary_markdown": str(md_path)}


def run_dgis_preflight(latitude: float, longitude: float, radius_m: int, region_id: str | None = None) -> dict[str, Any]:
    """Collect regional rubric catalog and API field survey before analysis.

    The preflight does not affect Excel directly. It only creates debug documents
    and raises a clear authorization error when the 2GIS key is blocked or invalid.
    """
    settings = get_settings()
    if settings.no_api:
        return {"status": "skipped", "reason": "no_api"}
    if not settings.dgis_api_key:
        raise DGISAuthorizationError("Не найден DGIS_API_KEY. Preflight 2GIS невозможен.")

    region = RegionRef(id=str(region_id or "").strip(), name="runtime_geocoder") if region_id else get_region_for_point(float(latitude), float(longitude))
    if not region.id:
        raise RuntimeError("2GIS preflight не смог определить region_id по точке анализа.")

    catalog = load_or_fetch_category_catalog(region_id=region.id, refresh=bool(settings.refresh_cache))
    catalog_paths = _save_catalog_documents(region, catalog, latitude=float(latitude), longitude=float(longitude))

    resolved_entries, missing_categories, resolved_region, resolved_catalog = resolve_configured_place_rubrics(
        settings.dgis_place_queries,
        latitude=float(latitude),
        longitude=float(longitude),
    )
    region = resolved_region if resolved_region.id else region
    catalog = resolved_catalog if resolved_catalog.get("items") else catalog

    entries = _preflight_scope_entries(catalog, resolved_entries)
    limit = _preflight_limit(default=0)
    if limit:
        entries = entries[:limit]

    all_items: list[dict[str, Any]] = []
    all_typed_items: list[dict[str, Any]] = []
    rubric_samples: list[dict[str, Any]] = []
    type_samples: list[dict[str, Any]] = []
    page_size = int(settings.config.get("dgis", {}).get("preflight_page_size", PREFLIGHT_SAMPLE_PAGE_SIZE) or PREFLIGHT_SAMPLE_PAGE_SIZE)

    for entry in entries:
        rubric_id = str(entry.get("rubric_id") or "").strip()
        category = str(entry.get("category") or entry.get("rubric_name") or rubric_id).strip()
        if not rubric_id:
            continue
        items, _raw = _sample_rubric_objects(
            region_id=region.id,
            rubric_id=rubric_id,
            latitude=float(latitude),
            longitude=float(longitude),
            radius_m=int(radius_m),
            page_size=page_size,
        )
        if items:
            all_items.extend(items)
            observe_items(
                region_id=region.id,
                source="preflight_rubric_sample_items",
                object_kind=f"rubric_{rubric_id}",
                items=items,
                request_params={"rubric_id": rubric_id, "radius_m": int(radius_m), "page_size": page_size},
            )
            observe_object_dictionary(
                region_id=region.id,
                source="preflight_rubric_sample_items",
                object_kind=f"rubric_{rubric_id}",
                items=items,
                request_params={"rubric_id": rubric_id, "radius_m": int(radius_m), "page_size": page_size},
            )
        rubric_samples.append(
            {
                "rubric_id": rubric_id,
                "category": category,
                "objects_sampled": len(items),
                "status": "ok" if items else "empty_or_unavailable_near_location",
            }
        )
        time.sleep(PREFLIGHT_SLEEP_SEC)

    for object_type, fields in TYPED_OBJECTS.items():
        items = _sample_typed_objects(
            region_id=region.id,
            object_type=object_type,
            fields=fields,
            latitude=float(latitude),
            longitude=float(longitude),
            radius_m=int(radius_m),
            page_size=page_size,
        )
        if items:
            all_typed_items.extend(items)
        type_samples.append({"type": object_type, "objects_sampled": len(items), "status": "ok" if items else "empty_or_unavailable_near_location"})
        time.sleep(PREFLIGHT_SLEEP_SEC)

    if all_items:
        observe_items(
            region_id=region.id,
            source="preflight_location_dataset",
            object_kind="all_sampled_objects",
            items=all_items,
            request_params={"radius_m": int(radius_m), "rubrics": len(entries), "page_size": page_size},
        )
        observe_object_dictionary(
            region_id=region.id,
            source="preflight_location_dataset",
            object_kind="all_sampled_objects",
            items=all_items,
            request_params={"radius_m": int(radius_m), "rubrics": len(entries), "page_size": page_size},
        )

    if all_typed_items:
        observe_items(
            region_id=region.id,
            source="preflight_typed_object_dataset",
            object_kind="all_typed_objects",
            items=all_typed_items,
            request_params={"radius_m": int(radius_m), "types": list(TYPED_OBJECTS)},
        )
        observe_object_dictionary(
            region_id=region.id,
            source="preflight_typed_object_dataset",
            object_kind="all_typed_objects",
            items=all_typed_items,
            request_params={"radius_m": int(radius_m), "types": list(TYPED_OBJECTS)},
        )

    summary = {
        "generated_at": _now(),
        "version": PREFLIGHT_VERSION,
        "status": "ok",
        "region_id": region.id,
        "region_name": region.name,
        "catalog_rubrics_count": len(catalog.get("items", [])) if isinstance(catalog.get("items"), list) else 0,
        "configured_missing_categories": missing_categories,
        "rubric_scope": str(os.getenv("GEO_ANALYZER_DGIS_PREFLIGHT_SCOPE") or settings.config.get("dgis", {}).get("preflight_scope", "configured")),
        "sampled_rubrics_count": len(entries),
        "sampled_objects_count": len(all_items),
        "sampled_typed_objects_count": len(all_typed_items),
        "rubric_samples": rubric_samples,
        "type_samples": type_samples,
        "paths": catalog_paths,
    }
    summary_paths = _save_summary(region, summary)
    summary["paths"].update(summary_paths)
    logger.info(
        "2GIS preflight completed: region_id=%s catalog_rubrics=%s sampled_rubrics=%s sampled_objects=%s typed_objects=%s",
        region.id,
        summary["catalog_rubrics_count"],
        summary["sampled_rubrics_count"],
        summary["sampled_objects_count"],
        summary["sampled_typed_objects_count"],
    )
    return summary


__all__ = ["DGISAuthorizationError", "run_dgis_preflight"]
