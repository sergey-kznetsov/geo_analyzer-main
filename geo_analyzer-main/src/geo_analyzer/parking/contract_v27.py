from __future__ import annotations

"""2GIS parking/building runtime contract.

No q-search. Candidates remain only 2GIS type=parking and type=building.
Raw technical API fields are profiled into debug logs, not Excel sheets.
"""

import importlib
import json
import math
import re
import time
from typing import Any

import pandas as pd
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from geo_analyzer.ingestion.dgis.api_profile import observe_items

_fixes = importlib.import_module("geo_analyzer.parking.fixes")
_supply = _fixes._supply

PARKING_LOADER_VERSION = "parking_v29_detail_card_profiled_before_calc"
_ORIGINAL_RUNTIME_PATCH = _fixes._apply_runtime_patch
DETAIL_CARD_SLEEP_SEC = 0.08

PARKING_FIELDS = (
    "items.id,items.external_id,items.type,items.subtype,items.point,items.geometry,"
    "items.geometry.hull,items.geometry.centroid,items.geometry.selection,"
    "items.address_name,items.full_address_name,items.rubrics,items.name,items.attribute_groups,"
    "items.access,items.access_comment,items.access_type,items.capacity,items.is_paid,items.cost,"
    "items.purpose,items.purpose_name,items.level_count,items.parking,items.links,items.description,items.statistics"
)

BUILDING_FIELDS = (
    "items.id,items.external_id,items.type,items.subtype,items.point,items.geometry,"
    "items.geometry.hull,items.geometry.centroid,items.geometry.selection,"
    "items.address_name,items.full_address_name,items.rubrics,items.name,items.attribute_groups,"
    "items.description,items.links,items.links.database_entrances.apartments_info,items.statistics,"
    "items.floors,items.floor_count,items.storeys,items.level_count,"
    "items.structure_info,items.structure_info.apartments_count,items.structure_info.porch_count,"
    "items.structure_info.floors,items.structure_info.floor_count,items.structure_info.floor_type,"
    "items.structure_info.year_of_construction,items.structure_info.elevators_count,"
    "items.flat_count,items.flats,items.apartments,items.apartment_count,"
    "items.entrance_count,items.entrances,items.purpose,items.purpose_name"
)

RESTRICTED_ACCESS = (
    "только для резидент", "для резидент", "только для жителей", "для жителей", "для жильцов",
    "жильцам", "резидентам", "дворовая", "дворовые", "частная", "частный", "приват",
    "закрытая", "закрытый", "по пропуск", "пропускн", "шлагбаум", "доступ ограничен",
    "только для сотрудников", "для сотрудников", "служебная", "служебный", "для персонала",
    "только для посетителей", "для посетителей", "resident", "private", "staff only", "visitors only",
)

PUBLIC_ACCESS = (
    "общедоступ", "общественная", "общественный", "публичная", "публичный", "городская",
    "городской", "муниципальная", "муниципальный", "уличная", "уличный", "public", "street parking",
)

PROJECT_CARD = ("жилой комплекс", "жк ", " жк", "новострой", "строящийся", "строящ", "офис продаж", "отдел продаж")
NON_RESIDENTIAL = tuple(_supply.NON_RESIDENTIAL_PURPOSE_MARKERS) + tuple(_supply.NON_RESIDENTIAL_BUILDING_NAME_MARKERS) + tuple(getattr(_fixes, "EXTRA_NON_RESIDENTIAL_BUILDING_MARKERS", ()))


def _missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _txt(value: Any) -> str:
    if _missing(value):
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str).replace("ё", "е").lower()
    return str(value).replace("ё", "е").lower().strip()


def _parse_geometry(value: Any) -> BaseGeometry | None:
    if _missing(value):
        return None
    if isinstance(value, BaseGeometry):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return _supply.shapely_wkt.loads(text)
        except Exception:
            pass
        try:
            return _parse_geometry(json.loads(text))
        except Exception:
            return None
    if isinstance(value, dict):
        lat = value.get("lat") or value.get("latitude")
        lon = value.get("lon") or value.get("lng") or value.get("longitude")
        if not _missing(lat) and not _missing(lon):
            try:
                return Point(float(lon), float(lat))
            except (TypeError, ValueError):
                pass
        for key in ("hull", "selection", "centroid", "center", "point", "wkt", "value", "geometry"):
            if key in value:
                geom = _parse_geometry(value.get(key))
                if geom is not None:
                    return geom
        if "type" in value and "coordinates" in value:
            try:
                return shape(value)
            except Exception:
                return None
    return None


def _point(item: dict[str, Any]) -> tuple[float | None, float | None]:
    raw = item.get("point") or {}
    if isinstance(raw, dict) and not _missing(raw.get("lat")) and not _missing(raw.get("lon")):
        try:
            return float(raw.get("lat")), float(raw.get("lon"))
        except (TypeError, ValueError):
            pass
    geom = _parse_geometry(item.get("geometry"))
    if geom is None:
        return None, None
    try:
        rep = geom if isinstance(geom, Point) else geom.representative_point()
        return float(rep.y), float(rep.x)
    except Exception:
        return None, None


def _attr_text(value: Any) -> str:
    return _txt(value)


def _row_access_text(row: pd.Series) -> str:
    parts = []
    for col in (
        "access_2gis", "access_comment_2gis", "access_type_2gis", "purpose_2gis", "purpose_name_2gis",
        "description_2gis", "parking_2gis", "attribute_groups", "raw_2gis",
    ):
        if col in row.index:
            parts.append(_txt(row.get(col)))
    return " ".join(parts)


def _type_marker(row: pd.Series) -> str:
    return " ".join(_txt(row.get(col)) for col in ("Поисковый_запрос", "rubric_id") if col in row.index)


def _is_type_row(row: pd.Series, kind: str, marker: str) -> bool:
    return _txt(row.get("_semantic_kind")) == kind and marker in _type_marker(row)


def _first_value(*values: Any) -> Any:
    for value in values:
        if not _missing(value):
            return value
    return None


def _structure_info(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("structure_info")
    return value if isinstance(value, dict) else {}


def _database_apartments_info(item: dict[str, Any]) -> Any:
    links = item.get("links")
    if not isinstance(links, dict):
        return None
    entrances = links.get("database_entrances")
    if isinstance(entrances, dict):
        return entrances.get("apartments_info")
    if isinstance(entrances, list):
        return [entry.get("apartments_info") for entry in entrances if isinstance(entry, dict)]
    return None


def _recursive_int(value: Any, keys: tuple[str, ...], min_value: int = 1, max_value: int = 10000) -> int | None:
    if _missing(value):
        return None
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                parsed = _supply._safe_int(value.get(key))
                if parsed is not None and min_value <= parsed <= max_value:
                    return parsed
        for child in value.values():
            parsed = _recursive_int(child, keys, min_value, max_value)
            if parsed is not None:
                return parsed
    if isinstance(value, list):
        candidates: list[int] = []
        for child in value:
            parsed = _recursive_int(child, keys, min_value, max_value)
            if parsed is not None:
                candidates.append(parsed)
        return sum(candidates) if candidates else None
    parsed = _supply._safe_int(value)
    if parsed is not None and min_value <= parsed <= max_value:
        return parsed
    return None


def _item_apartments(item: dict[str, Any]) -> Any:
    structure = _structure_info(item)
    direct = _first_value(
        item.get("flat_count"), item.get("flats"), item.get("apartments"), item.get("apartment_count"),
        item.get("apartments_count"), structure.get("apartments_count"), structure.get("flat_count"),
    )
    if not _missing(direct):
        return direct
    return _recursive_int(_database_apartments_info(item), ("apartments_count", "apartment_count", "flat_count", "flats", "apartments"), _supply.MIN_RELIABLE_APARTMENTS, _supply.MAX_PLAUSIBLE_APARTMENTS)


def _item_floors(item: dict[str, Any]) -> Any:
    structure = _structure_info(item)
    return _first_value(
        item.get("floors"), item.get("floor_count"), item.get("storeys"), item.get("level_count"),
        structure.get("floors"), structure.get("floor_count"), structure.get("storeys"), structure.get("level_count"),
    )


def _item_entrances(item: dict[str, Any]) -> Any:
    structure = _structure_info(item)
    direct = _first_value(item.get("entrance_count"), item.get("entrances"), structure.get("porch_count"), structure.get("entrance_count"), structure.get("entrances"))
    if not _missing(direct):
        return direct
    return _recursive_int(_database_apartments_info(item), ("porch_count", "entrance_count", "entrances_count", "entrances"), 1, _supply.MAX_ENTRANCES)


def _is_parking(row: pd.Series) -> bool:
    if not _is_type_row(row, "parking", "type:parking"):
        return False
    name = _txt([row.get("Название") if "Название" in row.index else "", row.get("Категория_2GIS") if "Категория_2GIS" in row.index else ""])
    if any(marker in name for marker in getattr(_fixes, "DROP_OFF_MARKERS", ())):
        return False
    return True


def _is_building(row: pd.Series) -> bool:
    if not _is_type_row(row, "residential", "type:building"):
        return False
    text = _txt([row.get(c) for c in ("Название", "Адрес", "Категория_2GIS", "purpose_2gis", "purpose_name_2gis", "rubrics_2gis") if c in row.index])
    if any(marker in text for marker in PROJECT_CARD):
        return False
    if any(_txt(marker) in text for marker in NON_RESIDENTIAL if _txt(marker)):
        return False
    return True


def _is_paid(row: pd.Series) -> bool | None:
    value = row.get("is_paid_2gis") if "is_paid_2gis" in row.index else None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "да"}:
            return True
        if low in {"false", "0", "no", "нет"}:
            return False
    return None


def _checked(row: pd.Series) -> bool:
    if "parking_capacity_checked_2gis" in row.index and row.get("parking_capacity_checked_2gis") is True:
        return True
    return any(col in row.index and not _missing(row.get(col)) for col in ("capacity_2gis", "access_2gis", "access_comment_2gis", "access_type_2gis", "is_paid_2gis", "parking_2gis", "attribute_groups", "raw_2gis"))


def _classify(row: pd.Series) -> tuple[str, str, bool, str, str, str, bool]:
    access = _row_access_text(row)
    buyable = _supply._can_buy_space(_supply._row_text(row) + " " + access)
    if any(marker in access for marker in RESTRICTED_ACCESS):
        return "Исключена из расчёта", "Ограниченный доступ", False, "2GIS указывает ограничение доступа: резиденты/жильцы/сотрудники/посетители/пропуск/закрытая территория", "Ограниченная парковка", "excluded_restricted_access", buyable
    paid = _is_paid(row)
    paid_text = _supply._has_paid_signal(access)
    public = any(marker in access for marker in PUBLIC_ACCESS)
    if paid is True or paid_text:
        return "Платная", "Общедоступная", True, "", "2GIS: платная парковка", "included_paid", buyable
    if buyable:
        return "Неизвестно", "Доступно по покупке/аренде", True, "", "2GIS: покупка/аренда машиноместа", "included_buyable", buyable
    if paid is False:
        return "Бесплатная", "Общедоступная", True, "", "2GIS: бесплатная парковка", "included_free", buyable
    if public or _checked(row):
        return "Неизвестно", "Общедоступная", True, "", "2GIS: type=parking без признаков ограничения доступа", "included_public_unknown_payment", buyable
    return "Исключена из расчёта", "Публичность не подтверждена", False, "Нет признаков публичности или вместимости в 2GIS", "Не подтверждена общедоступность", "excluded_no_public_evidence", buyable


def _fetch_detail_card(item_id: str, *, kind: str, region_id: str, settings: Any, fields: str) -> dict[str, Any] | None:
    if not item_id:
        return None
    base = settings.dgis_catalog_url.rstrip("/")
    variants = [
        (f"{base}/3.0/items/byid", {"id": item_id, "region_id": region_id, "fields": fields, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items/byid", {"id": item_id, "fields": fields, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items", {"id": item_id, "region_id": region_id, "fields": fields, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items", {"id": item_id, "fields": fields, "key": settings.dgis_api_key}),
        (f"{base}/3.0/items/byid", {"id": item_id, "key": settings.dgis_api_key}),
    ]
    for url, params in variants:
        try:
            response = _supply.requests.get(url, params=params, timeout=settings.dgis_timeout)
            data = response.json()
            meta = data.get("meta", {}) if isinstance(data, dict) else {}
            code = int(meta.get("code", response.status_code))
            if code >= 400:
                continue
            items = _supply._extract_items(data if isinstance(data, dict) else {})
            if not items:
                continue
            observe_items(
                region_id=region_id,
                source="parking_supply_detail_card",
                object_kind=f"{kind}_detail",
                items=items,
                request_params={k: v for k, v in params.items() if k != "key"},
                raw_response=data if isinstance(data, dict) else None,
            )
            return items[0]
        except Exception:
            continue
    return None


def _merge_item_with_detail(item: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(detail, dict) or not detail:
        return item
    merged = dict(item)
    for key, value in detail.items():
        if _missing(value):
            continue
        if key not in merged or _missing(merged.get(key)):
            merged[key] = value
        elif isinstance(merged.get(key), dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update({k: v for k, v in value.items() if not _missing(v)})
            merged[key] = nested
    return merged


def _enrich_with_detail_cards(items: list[dict[str, Any]], *, kind: str, region_id: str, settings: Any, fields: str) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        detail = _fetch_detail_card(item_id, kind=kind, region_id=region_id, settings=settings, fields=fields) if item_id else None
        enriched.append(_merge_item_with_detail(item, detail))
        if item_id:
            time.sleep(DETAIL_CARD_SLEEP_SEC)
    return enriched


def _absorb(rows: list[dict[str, Any]], *, items: list[dict[str, Any]], kind: str, rubric_id: str, rubric_label: str, region_id: str, catalog: dict[str, Any], query_label: str) -> None:
    observe_items(
        region_id=region_id,
        source="parking_supply_candidates",
        object_kind=kind,
        items=[item for item in items if isinstance(item, dict)],
        request_params={"query_label": query_label, "rubric_id": rubric_id},
    )
    for item in items:
        lat, lon = _point(item)
        rubrics = item.get("rubrics") or []
        names = [str(r.get("name")) for r in rubrics if isinstance(r, dict) and r.get("name")] or [rubric_label]
        geom = item.get("geometry") if isinstance(item.get("geometry"), dict) else {}
        rows.append({
            "dgis_id": item.get("id"), "fid": item.get("external_id"), "object_type_2gis": item.get("type"),
            "Название": item.get("name") or item.get("caption") or rubric_label,
            "Адрес": item.get("address_name") or item.get("full_address_name"),
            "Категория_2GIS": names[0], "Категория_2GIS_официальная": names[0], "source_categories_2gis": names, "rubrics_2gis": names,
            "rubric_id": rubric_id, "resolved_region_id": region_id, "Поисковый_запрос": query_label, "_semantic_kind": kind,
            "Широта": lat, "Долгота": lon, "attribute_groups": item.get("attribute_groups"),
            "geometry_hull": geom.get("hull") or geom.get("selection") or geom.get("centroid") or item.get("geometry") or item.get("point"),
            "access_2gis": item.get("access"), "access_comment_2gis": item.get("access_comment"), "access_type_2gis": item.get("access_type"),
            "capacity_2gis": item.get("capacity"), "is_paid_2gis": item.get("is_paid"), "cost_2gis": item.get("cost"), "parking_2gis": item.get("parking"),
            "purpose_2gis": item.get("purpose"), "purpose_name_2gis": item.get("purpose_name"), "level_count_2gis": item.get("level_count"),
            "links_2gis": item.get("links"), "description_2gis": item.get("description"), "statistics_2gis": item.get("statistics"),
            "structure_info_2gis": item.get("structure_info"), "apartments_info_2gis": _database_apartments_info(item),
            "floors_2gis": _item_floors(item), "flat_count_2gis": _item_apartments(item), "entrance_count_2gis": _item_entrances(item), "raw_2gis": item,
        })


def _offsets(lat: float, lon: float, radius: int) -> list[tuple[float, float]]:
    d = max(float(radius), 250.0) / 111320.0 * 0.45
    dl = d / max(math.cos(math.radians(lat)), 0.2)
    return [(lat, lon), (lat+d, lon), (lat-d, lon), (lat, lon+dl), (lat, lon-dl), (lat+d, lon+dl), (lat+d, lon-dl), (lat-d, lon+dl), (lat-d, lon-dl)]


def _load_parkings(*, latitude: float, longitude: float, radius: int, region_id: str, settings: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    point = f"{float(longitude)},{float(latitude)}"
    params = {"region_id": region_id, "type": "parking", "point": point, "location": point, "radius": int(radius), "page_size": _supply.SEMANTIC_PAGE_SIZE, "fields": PARKING_FIELDS, "key": settings.dgis_api_key, "sort": "distance"}
    items = _supply._fetch_2gis_items(url, params, _supply.PARKING_TYPE_MAX_PAGES, settings.dgis_timeout)
    observe_items(region_id=region_id, source="parking_supply_type_query", object_kind="parking", items=items, request_params={k: v for k, v in params.items() if k != "key"})
    items = _enrich_with_detail_cards(items, kind="parking", region_id=region_id, settings=settings, fields=PARKING_FIELDS)
    _absorb(rows, items=items, kind="parking", rubric_id="type:parking", rubric_label="Парковка", region_id=region_id, catalog=catalog, query_label="type:parking")
    return rows


def _load_buildings(*, latitude: float, longitude: float, radius: int, region_id: str, settings: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    for lat, lon in _offsets(float(latitude), float(longitude), int(radius)):
        point = f"{lon},{lat}"
        params = {"region_id": region_id, "type": "building", "point": point, "location": point, "radius": int(radius), "page_size": _supply.BUILDING_TYPE_PAGE_SIZE, "fields": BUILDING_FIELDS, "key": settings.dgis_api_key, "sort": "distance"}
        items = _supply._fetch_2gis_items(url, params, _supply.BUILDING_TYPE_MAX_PAGES, settings.dgis_timeout)
        if not items:
            params.pop("region_id", None)
            items = _supply._fetch_2gis_items(url, params, _supply.BUILDING_TYPE_MAX_PAGES, settings.dgis_timeout)
        observe_items(region_id=region_id, source="parking_supply_type_query", object_kind="building", items=items, request_params={k: v for k, v in params.items() if k != "key"})
        unique = []
        for item in items:
            item_id = str(item.get("id") or "").strip()
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            unique.append(item)
        unique = _enrich_with_detail_cards(unique, kind="building", region_id=region_id, settings=settings, fields=BUILDING_FIELDS)
        _absorb(rows, items=unique, kind="residential", rubric_id="type:building", rubric_label="Жилой дом", region_id=region_id, catalog=catalog, query_label="type:building")
    return rows


def _parking_fields() -> str:
    return PARKING_FIELDS


def _building_fields() -> str:
    return BUILDING_FIELDS


def _no_clip_score(value: float) -> float:
    try:
        if math.isnan(float(value)):
            return math.nan
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return math.nan


def _apply_once() -> None:
    _supply.PARKING_LOADER_VERSION = PARKING_LOADER_VERSION
    _fixes.PARKING_LOADER_VERSION = PARKING_LOADER_VERSION
    _fixes.RESIDENTIAL_COUNT_FIELDS = BUILDING_FIELDS
    _supply._parse_geometry = _parse_geometry
    _supply._clip_score = _no_clip_score
    _supply._parking_fields = _parking_fields
    _supply._residential_fields = _building_fields
    _supply._absorb_2gis_items = _absorb
    _supply._building_is_residential = lambda item: True
    _supply._load_type_parking_objects = _load_parkings
    _supply._load_type_building_residential = _load_buildings
    _supply._is_residential_building = _is_building
    _supply._is_parking_object = _is_parking
    _supply._classify_parking_type = _classify
    _fixes._building_is_residential_fixed = lambda item: True
    _fixes._load_type_parking_objects_fixed = _load_parkings
    _fixes._load_type_building_residential_fixed = _load_buildings
    _fixes._is_residential_building_fixed = _is_building
    _fixes._is_parking_object_fixed = _is_parking
    _fixes._classify_parking_type_fixed = _classify
    _fixes._capacity_was_checked = _checked


def apply_2gis_contract_v27() -> None:
    def patched_runtime_patch() -> None:
        _ORIGINAL_RUNTIME_PATCH()
        _apply_once()
    _fixes._apply_runtime_patch = patched_runtime_patch
    patched_runtime_patch()


__all__ = ["PARKING_LOADER_VERSION", "apply_2gis_contract_v27"]
