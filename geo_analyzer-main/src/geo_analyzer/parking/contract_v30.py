from __future__ import annotations

"""Resilient 2GIS building-loader contract.

This layer keeps the strict rule from v27: residential candidates for the
parking formula come only from 2GIS ``type=building`` object responses and their
object cards by ``id``. It adds several structural request forms supported by
Places API so the loader is not tied to a single point/location parameter shape.
"""

from typing import Any

from geo_analyzer.parking import fixes as _fixes
from geo_analyzer.parking import contract_v27 as _v27

_supply = _fixes._supply

PARKING_LOADER_VERSION = "parking_v30_resilient_type_building_loader"

BUILDING_FIELDS = (
    "items.id,items.external_id,items.type,items.subtype,items.point,items.geometry,"
    "items.geometry.hull,items.geometry.centroid,items.geometry.selection,"
    "items.address,items.address_name,items.full_address_name,items.adm_div,items.rubrics,items.name,"
    "items.attribute_groups,items.description,items.links,items.links.database_entrances,"
    "items.links.database_entrances.apartments_info,items.statistics,items.has_apartments_info,"
    "items.floors,items.floor_count,items.storeys,items.level_count,items.purpose_code,"
    "items.structure_info,items.structure_info.apartments_count,items.structure_info.porch_count,"
    "items.structure_info.floors,items.structure_info.floor_count,items.structure_info.floor_type,"
    "items.structure_info.material,items.structure_info.year_of_construction,items.structure_info.elevators_count,"
    "items.flat_count,items.flats,items.apartments,items.apartment_count,"
    "items.entrance_count,items.entrances,items.purpose,items.purpose_name"
)


def _building_request_variants(*, latitude: float, longitude: float, radius: int, region_id: str, settings: Any) -> list[dict[str, Any]]:
    point = f"{float(longitude)},{float(latitude)}"
    common = {
        "type": "building",
        "radius": int(radius),
        "page_size": _supply.BUILDING_TYPE_PAGE_SIZE,
        "fields": BUILDING_FIELDS,
        "key": settings.dgis_api_key,
        "sort": "distance",
    }
    variants = [
        {**common, "region_id": region_id, "point": point, "location": point},
        {**common, "region_id": region_id, "point": point},
        {**common, "region_id": region_id, "lon": float(longitude), "lat": float(latitude)},
        {**common, "point": point, "location": point},
        {**common, "point": point},
        {**common, "lon": float(longitude), "lat": float(latitude)},
    ]
    return [params for params in variants if params.get("region_id") or "region_id" not in params]


def _load_buildings_v30(*, latitude: float, longitude: float, radius: int, region_id: str, settings: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"

    raw_total = 0
    enriched_total = 0
    for point_lat, point_lon in _v27._offsets(float(latitude), float(longitude), int(radius)):
        point_items: list[dict[str, Any]] = []
        for params in _building_request_variants(
            latitude=point_lat,
            longitude=point_lon,
            radius=int(radius),
            region_id=str(region_id or ""),
            settings=settings,
        ):
            items = _supply._fetch_2gis_items(url, params, _supply.BUILDING_TYPE_MAX_PAGES, settings.dgis_timeout)
            if items:
                point_items = items
                break

        raw_total += len(point_items)
        unique: list[dict[str, Any]] = []
        for item in point_items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            fallback_key = item_id or str(item.get("external_id") or item.get("address_name") or item.get("full_address_name") or item.get("point") or "")
            if fallback_key and fallback_key in seen_ids:
                continue
            if fallback_key:
                seen_ids.add(fallback_key)
            unique.append(item)

        if not unique:
            continue

        enriched = _v27._enrich_with_detail_cards(unique, kind="building", region_id=str(region_id or ""), settings=settings, fields=BUILDING_FIELDS)
        enriched_total += len(enriched)
        _v27._absorb(
            rows,
            items=enriched,
            kind="residential",
            rubric_id="type:building",
            rubric_label="Жилой дом",
            region_id=str(region_id or ""),
            catalog=catalog,
            query_label="type:building",
        )

    try:
        _supply.logger.info(
            "2GIS building loader v30: region_id=%s raw_items=%s enriched=%s rows=%s",
            region_id,
            raw_total,
            enriched_total,
            len(rows),
        )
    except Exception:
        pass
    return rows


def apply_2gis_contract_v30() -> None:
    _v27.apply_2gis_contract_v27()
    _supply.PARKING_LOADER_VERSION = PARKING_LOADER_VERSION
    _fixes.PARKING_LOADER_VERSION = PARKING_LOADER_VERSION
    _v27.PARKING_LOADER_VERSION = PARKING_LOADER_VERSION
    _fixes.RESIDENTIAL_COUNT_FIELDS = BUILDING_FIELDS
    _supply._residential_fields = lambda: BUILDING_FIELDS
    _supply._load_type_building_residential = _load_buildings_v30
    _fixes._load_type_building_residential_fixed = _load_buildings_v30


__all__ = ["PARKING_LOADER_VERSION", "apply_2gis_contract_v30"]
