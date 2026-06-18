from __future__ import annotations

"""Base parking supply module used by the strict 2GIS API-only patch.

This file must not import :mod:`geo_analyzer.parking.fixes`. The public package
``geo_analyzer.parking`` imports ``fixes``; ``fixes`` imports this module and
patches selected functions at runtime. Keeping this direction avoids circular
imports in the GUI and in PyInstaller builds.

The active parking-potential contract is implemented in ``parking.fixes``:
parking and residential candidates are generated only from 2GIS ``type=parking``
and ``type=building`` responses plus detailed object cards by ``id``.
"""

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
from shapely import wkt as shapely_wkt
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.category_catalog import (
    get_region_for_point,
    load_or_fetch_category_catalog,
    normalize_category_name,
    official_category_name_for_rubric_id,
)

logger = get_logger("geo_analyzer.parking.supply")

PARKING_LOADER_VERSION = "parking_base_runtime_patch"

CAR_OWNERSHIP_COEF = 0.8
FREE_PARKING_WEIGHT = 1.15
PAID_PARKING_WEIGHT = 0.60
UNKNOWN_PARKING_WEIGHT = 0.75
ZONE_WEIGHT_0_5 = 1.00
ZONE_WEIGHT_5_10 = 0.70

SEMANTIC_PAGE_SIZE = 10
PARKING_TYPE_MAX_PAGES = 50
BUILDING_TYPE_MAX_PAGES = 100
BUILDING_TYPE_PAGE_SIZE = 50
PARKING_QUERIES: list[str] = []
RESIDENTIAL_QUERIES: list[str] = []

RESIDENTIAL_PURPOSE_MARKERS = (
    "жилой дом",
    "жилые дома",
    "многоквартирный",
    "многоквартир",
    "жилой",
    "residential",
    "apartment",
)
NON_RESIDENTIAL_PURPOSE_MARKERS = (
    "административн",
    "торгов",
    "офисн",
    "промышлен",
    "складск",
    "производствен",
    "гостиниц",
    "медицинск",
    "образовательн",
    "спортивн",
)
NON_RESIDENTIAL_BUILDING_NAME_MARKERS = (
    "гараж", "гск", "паркинг", "автостоян", "стоянка",
    "завод", "фабрик", "склад", "ангар", "цех",
    "торгов", "магазин", "рынок", "молл",
    "больниц", "поликлиник", "госпиталь", "клиник",
    "школ", "лицей", "гимназия", "детский сад", "университет",
    "церковь", "храм", "собор", "мечеть",
    "офис", "бизнес-центр", "административн",
)

PAID_PARKING_MARKERS = {"платная", "оплата", "₽", "paid", "тариф", "платный"}
FREE_PARKING_MARKERS = {"бесплатная", "free"}
PAY_TEXT_FIELDS = (
    "Название", "name", "description_2gis", "description",
    "access_2gis", "access_comment_2gis", "purpose_2gis", "purpose_name_2gis",
    "Категория_2GIS", "Категория_2GIS_официальная", "attribute_groups",
)
UNDERGROUND_MARKERS = {"подземн", "underground"}
HARD_RESIDENTIAL_PARKING_MARKERS = {
    "только для резидент",
    "для резидент",
    "только для жителей",
    "для жильцов",
    "для жителей",
    "дворовая",
    "дворовые",
    "парковка жильцов",
    "парковка жителей",
    "жилой дом",
    "жилой комплекс",
}
RESIDENTIAL_PARKING_MARKERS = {"жилой", "жк", "многоквартир", "резидент"}
RESTRICTED_NON_RESIDENTIAL_MARKERS = {
    "для сотрудников", "служебная", "служебный", "только для сотрудников",
    "только для персонала", "корпоративная парковка",
}
CLOSED_PARKING_MARKERS = {
    "закрыт", "шлагбаум", "по пропуск", "пропускн", "приватн",
    "частная территория", "огорожен", "только для резидент",
    "только для жильцов дома", "доступ ограничен",
}
BUYABLE_SPACE_MARKERS = {
    "продажа машиномест", "продажа мест", "продаются машиномест",
    "продаются места", "купить машиноместо", "купить место",
    "машиноместа в продаже", "машиноместо в продаж", "аренда машиномест",
    "аренда мест", "машиноместо в аренду", "в аренду", "помесячная аренда", "абонемент",
}

EXCLUDED_OWNER_MARKERS = {
    "Торговля": ["тц", "трц", "трк", "торгов", "развлекательн", "молл", "mall", "гипермаркет", "супермаркет", "рынок", "рынк", "магазин", "универмаг", "ритейл"],
    "Промышленность": ["завод", "фабрик", "производств", "промышлен", "склад", "логистическ", "терминал", "цех", "депо"],
    "Офисы и администрация": ["офис", "бизнес-центр", "бц", "деловой центр", "администрац", "госучрежд", "мфц", "министерств", "налогов", "суд"],
    "Транспортная инфраструктура": ["вокзал", "аэропорт", "порт", "автостанц", "автовокзал", "жд станция", "железнодорожн", "метро"],
    "Досуг и услуги": ["кинотеатр", "театр", "ресторан", "кафе", "бар", "спортзал", "фитнес", "стадион", "арена", "отель", "гостиниц", "поликлиник", "клиник", "больниц", "школ", "детский сад", "университет"],
}
EXCLUDED_OWNER_EXACT_TOKENS = {"тц", "трц", "трк", "бц", "мфц", "бар", "кафе", "цех", "суд", "порт", "депо", "метро"}

DEFAULT_APARTMENTS_PER_BUILDING = 72
DEFAULT_ENTRANCES = 2
DEFAULT_FLATS_PER_FLOOR_PER_ENTRANCE = 4
DEFAULT_FLOORS = 9
MIN_RELIABLE_APARTMENTS = 5
MAX_FLOORS = 50
MAX_ENTRANCES = 16
MAX_FLATS_PER_ENTRANCE = 500
MAX_PLAUSIBLE_APARTMENTS = 1500
AREA_PER_PARKING_SPACE_M2 = 25.0
MIN_PARKING_AREA_M2 = 20.0

DISABLED_PARKING_CAPACITY_MARKERS = {"инвалид", "маломобиль", "мгн", "accessible", "disabled", "handicap", "wheelchair"}
TOTAL_CAPACITY_LABEL_MARKERS = {"capacity", "вместим", "машино", "машиномест", "парковочных мест", "парковочные места", "количество мест", "мест всего", "общее количество мест", "всего мест", "places_total", "total_places", "spaces_total", "total_spaces", "parking_spaces", "parking_places"}
DIRECT_CAPACITY_COLUMNS = ["capacity_2gis", "parking_spaces", "parking_capacity", "parking_places", "places_count", "spaces_count", "capacity", "capacity_total", "total_capacity", "spaces", "places", "Количество_мест", "Парковочных_мест", "Вместимость"]


@dataclass(slots=True)
class ParkingSupplyResult:
    summary: pd.DataFrame
    parking_details: pd.DataFrame
    residential_details: pd.DataFrame
    text_summary: str
    gui_label: str


def calculate_parking_supply(*, pois: pd.DataFrame | list[dict[str, Any]] | None = None, isochrones: pd.DataFrame | list[dict[str, Any]] | None, latitude: float | None = None, longitude: float | None = None, radius_m: int | None = None) -> ParkingSupplyResult:
    poi_df = _ensure_geometry(_to_dataframe(pois))
    iso_df = _normalize_isochrones(_to_dataframe(isochrones))
    if iso_df.empty:
        return _empty_result("Парковочный потенциал не рассчитан: нет геометрии изохрон.")

    api_df = _load_semantic_parking_and_residential(latitude, longitude, radius_m)
    if not api_df.empty:
        api_df = _ensure_geometry(api_df)

    if poi_df.empty and api_df.empty:
        return _empty_result("Парковочный потенциал не рассчитан: 2GIS не вернул данные по жилым домам и парковкам.")

    combined = pd.concat([poi_df, api_df], ignore_index=True, sort=False) if not api_df.empty else poi_df
    combined = _deduplicate(combined)
    residential_df = _build_residential_details(combined, iso_df)
    parking_df = _build_parking_details(combined, iso_df)
    summary_df = _build_parking_summary(residential_df, parking_df)
    text_summary, gui_label = _build_text_outputs(summary_df)
    return ParkingSupplyResult(summary_df, parking_df, residential_df, text_summary, gui_label)


def _to_dataframe(value: pd.DataFrame | list[dict[str, Any]] | None) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list):
        return pd.DataFrame(value)
    return pd.DataFrame()


def _ensure_geometry(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    data = df.copy()
    data["geometry"] = [_geometry_from_row(row) for _, row in data.iterrows()]
    data = data[data["geometry"].apply(lambda geom: isinstance(geom, BaseGeometry) and not geom.is_empty)].copy()
    return data.reset_index(drop=True)


def _geometry_from_row(row: pd.Series) -> BaseGeometry | None:
    for column in ("geometry", "geometry_wkt", "geometry_hull"):
        if column in row.index:
            geom = _parse_geometry(row.get(column))
            if geom is not None:
                return geom
    raw = row.get("raw_2gis") if "raw_2gis" in row.index else None
    if isinstance(raw, dict):
        geom = _parse_geometry((raw.get("geometry") or {}).get("hull"))
        if geom is not None:
            return geom
        raw_point = raw.get("point") or {}
        geom = _point_from_values(raw_point.get("lat"), raw_point.get("lon"))
        if geom is not None:
            return geom
    point_value = row.get("point") if "point" in row.index else None
    if isinstance(point_value, dict):
        geom = _point_from_values(point_value.get("lat"), point_value.get("lon"))
        if geom is not None:
            return geom
    lat_col = _first_existing_column(row.to_frame().T, ["lat", "latitude", "Широта"])
    lon_col = _first_existing_column(row.to_frame().T, ["lon", "lng", "longitude", "Долгота"])
    if lat_col and lon_col:
        return _point_from_values(row.get(lat_col), row.get(lon_col))
    return None


def _point_from_values(lat: Any, lon: Any) -> Point | None:
    try:
        if _is_missing(lat) or _is_missing(lon):
            return None
        return Point(float(lon), float(lat))
    except (TypeError, ValueError):
        return None


def _parse_geometry(value: Any) -> BaseGeometry | None:
    if _is_missing(value):
        return None
    if isinstance(value, BaseGeometry):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return shapely_wkt.loads(text)
        except Exception:
            pass
        try:
            return _parse_geometry(json.loads(text))
        except Exception:
            return None
    if isinstance(value, dict):
        for key in ("hull", "wkt", "value", "geometry"):
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


def _normalize_isochrones(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    data = _ensure_geometry(df)
    if data.empty:
        return pd.DataFrame()
    minute_col = _first_existing_column(data, ["minutes", "Минут_пешком", "time_min", "duration"])
    if not minute_col:
        return pd.DataFrame()
    data["Минут_пешком"] = pd.to_numeric(data[minute_col], errors="coerce")
    data = data[data["Минут_пешком"].isin([5, 10, 15])].copy()
    return data.sort_values("Минут_пешком").reset_index(drop=True)


def _cache_path(latitude: float, longitude: float, radius_m: int) -> Path:
    settings = get_settings()
    return settings.cache_dir / "parking_supply" / f"base_{round(latitude, 6)}_{round(longitude, 6)}_{int(radius_m)}.json"


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]
    if isinstance(data.get("items"), list):
        return data["items"]
    return []


def _extract_total(data: dict[str, Any]) -> int | None:
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("total", "total_count", "items_count"):
            try:
                value = result.get(key)
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _fetch_2gis_items(url: str, base_params: dict[str, Any], max_pages: int, timeout: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    total: int | None = None
    page_size = int(base_params.get("page_size") or SEMANTIC_PAGE_SIZE)
    for page in range(1, max_pages + 1):
        params = dict(base_params, page=page)
        items: list[dict[str, Any]] | None = None
        raw_data: dict[str, Any] = {}
        for attempt in range(3):
            try:
                response = requests.get(url, params=params, timeout=timeout)
                data = response.json()
                raw_data = data if isinstance(data, dict) else {}
                meta = raw_data.get("meta", {})
                code = int(meta.get("code", response.status_code))
                if code == 429 or code >= 500:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if code == 404:
                    return collected
                if code >= 400:
                    logger.warning("2GIS parking query error params=%s raw=%s", {k: v for k, v in params.items() if k != "key"}, json.dumps(raw_data, ensure_ascii=False)[:500])
                    return collected
                response.raise_for_status()
                items = _extract_items(raw_data)
                if total is None:
                    total = _extract_total(raw_data)
                break
            except Exception as exc:
                logger.warning("2GIS parking query failed page=%s attempt=%s: %s", page, attempt, exc)
                time.sleep(0.3 * (attempt + 1))
        if items is None or not items:
            break
        collected.extend(items)
        if total is not None and len(collected) >= total:
            break
        if len(items) < page_size:
            break
        time.sleep(0.12)
    return collected


def _parking_fields() -> str:
    return (
        "items.id,items.external_id,items.point,items.geometry.hull,items.address_name,"
        "items.full_address_name,items.rubrics,items.name,items.attribute_groups,items.schedule,"
        "items.context,items.access,items.access_comment,items.capacity,items.is_paid,"
        "items.for_trucks,items.paving_type,items.is_incentive,items.purpose,"
        "items.purpose_name,items.level_count,items.links,items.description,items.statistics"
    )


def _residential_fields() -> str:
    return (
        "items.id,items.external_id,items.point,items.geometry.hull,items.address_name,"
        "items.full_address_name,items.rubrics,items.name,items.attribute_groups,"
        "items.description,items.links,items.statistics,"
        "items.floors,items.floor_count,items.storeys,"
        "items.flat_count,items.flats,items.apartments,"
        "items.entrance_count,items.entrances,items.purpose,items.purpose_name"
    )


def _absorb_2gis_items(rows: list[dict[str, Any]], *, items: list[dict[str, Any]], kind: str, rubric_id: str, rubric_label: str, region_id: str, catalog: dict[str, Any], query_label: str) -> None:
    for item in items:
        item_point = item.get("point") or {}
        rubrics = item.get("rubrics") or []
        rubric_ids = [str(r.get("id")) for r in rubrics if isinstance(r, dict) and r.get("id")]
        rubric_names: list[str] = []
        for rid in rubric_ids:
            official = official_category_name_for_rubric_id(rid, catalog)
            if official and official not in rubric_names:
                rubric_names.append(official)
        for rubric in rubrics:
            if isinstance(rubric, dict) and rubric.get("name") and str(rubric.get("name")) not in rubric_names:
                rubric_names.append(str(rubric.get("name")))
        if not rubric_names:
            rubric_names = [rubric_label]
        rows.append({
            "dgis_id": item.get("id"),
            "fid": item.get("external_id"),
            "Название": item.get("name") or item.get("caption") or rubric_label,
            "Адрес": item.get("address_name") or item.get("full_address_name"),
            "Категория_2GIS": rubric_names[0],
            "Категория_2GIS_официальная": rubric_names[0],
            "source_categories_2gis": rubric_names,
            "rubrics_2gis": rubric_names,
            "category_groups_2gis": rubric_ids or ([rubric_id] if rubric_id else []),
            "rubric_id": rubric_id,
            "resolved_region_id": region_id,
            "Поисковый_запрос": query_label,
            "_semantic_kind": kind,
            "Широта": item_point.get("lat"),
            "Долгота": item_point.get("lon"),
            "attribute_groups": item.get("attribute_groups"),
            "geometry_hull": (item.get("geometry") or {}).get("hull"),
            "access_2gis": item.get("access"),
            "access_comment_2gis": item.get("access_comment"),
            "capacity_2gis": item.get("capacity"),
            "is_paid_2gis": item.get("is_paid"),
            "for_trucks_2gis": item.get("for_trucks"),
            "paving_type_2gis": item.get("paving_type"),
            "is_incentive_2gis": item.get("is_incentive"),
            "purpose_2gis": item.get("purpose"),
            "purpose_name_2gis": item.get("purpose_name"),
            "level_count_2gis": item.get("level_count"),
            "links_2gis": item.get("links"),
            "description_2gis": item.get("description"),
            "statistics_2gis": item.get("statistics"),
            "floors_2gis": item.get("floors") or item.get("floor_count") or item.get("storeys"),
            "flat_count_2gis": item.get("flat_count") or item.get("flats") or item.get("apartments"),
            "entrance_count_2gis": item.get("entrance_count") or item.get("entrances"),
            "raw_2gis": item,
        })


def _load_type_parking_objects(*, latitude: float, longitude: float, radius: int, region_id: str, settings: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    point = f"{float(longitude)},{float(latitude)}"
    params = {
        "region_id": region_id,
        "type": "parking",
        "point": point,
        "location": point,
        "radius": radius,
        "page_size": SEMANTIC_PAGE_SIZE,
        "fields": _parking_fields(),
        "key": settings.dgis_api_key,
        "sort": "distance",
    }
    items = _fetch_2gis_items(url, params, PARKING_TYPE_MAX_PAGES, settings.dgis_timeout)
    _absorb_2gis_items(rows, items=items, kind="parking", rubric_id="type:parking", rubric_label="Парковка", region_id=region_id, catalog=catalog, query_label="type:parking")
    return rows


def _load_type_building_residential(*, latitude: float, longitude: float, radius: int, region_id: str, settings: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    point = f"{float(longitude)},{float(latitude)}"
    params = {
        "region_id": region_id,
        "type": "building",
        "point": point,
        "location": point,
        "radius": radius,
        "page_size": BUILDING_TYPE_PAGE_SIZE,
        "fields": _residential_fields(),
        "key": settings.dgis_api_key,
        "sort": "distance",
    }
    items = _fetch_2gis_items(url, params, BUILDING_TYPE_MAX_PAGES, settings.dgis_timeout)
    _absorb_2gis_items(rows, items=items, kind="residential", rubric_id="type:building", rubric_label="Жилой дом", region_id=region_id, catalog=catalog, query_label="type:building")
    return rows


def _load_semantic_parking_and_residential(latitude: float | None, longitude: float | None, radius_m: int | None) -> pd.DataFrame:
    return pd.DataFrame()


def _deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    data = df.copy()
    for column in ["dgis_id", "Название", "Адрес", "Широта", "Долгота", "_semantic_kind"]:
        if column not in data.columns:
            data[column] = pd.NA
    dgis_key = data["dgis_id"].fillna("").astype(str).str.strip()
    kind_key = data["_semantic_kind"].fillna("").astype(str).str.strip().str.lower()
    fallback = data["Название"].fillna("").astype(str).str.lower().str.strip() + "|" + data["Адрес"].fillna("").astype(str).str.lower().str.strip() + "|" + data["Широта"].fillna("").astype(str) + "|" + data["Долгота"].fillna("").astype(str)
    data["_dedupe_key"] = (kind_key + "|" + dgis_key).where(dgis_key.ne(""), kind_key + "|" + fallback)
    return data.drop_duplicates(subset=["_dedupe_key"], keep="first").drop(columns=["_dedupe_key"], errors="ignore").reset_index(drop=True)


def _detect_zone(geometry: BaseGeometry | None, iso_df: pd.DataFrame, *, max_minutes: int = 10) -> tuple[str | None, int | None]:
    if geometry is None:
        return None, None
    iso5 = iso_df[iso_df["Минут_пешком"] == 5]
    iso10 = iso_df[iso_df["Минут_пешком"] == 10]
    if not iso5.empty and any(_geometry_contains(poly, geometry) for poly in iso5["geometry"]):
        return "0–5 минут", 5
    if max_minutes >= 10 and not iso10.empty and any(_geometry_contains(poly, geometry) for poly in iso10["geometry"]):
        return "5–10 минут", 10
    return None, None


def _geometry_contains(container: Any, geometry: BaseGeometry) -> bool:
    if not isinstance(container, BaseGeometry):
        return False
    try:
        return bool(container.contains(geometry) or container.intersects(geometry))
    except Exception:
        return False


def _is_residential_building(row: pd.Series) -> bool:
    return False


def _is_parking_object(row: pd.Series) -> bool:
    return False


def _can_buy_space(text: str) -> bool:
    return any(marker in text for marker in BUYABLE_SPACE_MARKERS)


def _is_paid_field(row: pd.Series) -> bool | None:
    if "is_paid_2gis" not in row.index:
        return None
    value = row.get("is_paid_2gis")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "да", "yes"}:
            return True
        if low in {"false", "0", "нет", "no"}:
            return False
    return None


def _pay_text(row: pd.Series) -> str:
    parts: list[str] = []
    for column in PAY_TEXT_FIELDS:
        if column not in row.index:
            continue
        value = row.get(column)
        if _is_missing(value):
            continue
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
        else:
            parts.append(json.dumps(value, ensure_ascii=False, default=str))
    return " ".join(parts).replace("ё", "е").lower()


def _narrow_owner_text(row: pd.Series) -> str:
    parts: list[str] = []
    for column in ("Название", "name", "Категория_2GIS", "Категория_2GIS_официальная", "rubrics_2gis", "source_categories_2gis"):
        if column not in row.index:
            continue
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(v) for v in value if isinstance(v, str) and str(v).strip())
    return " ".join(parts).replace("ё", "е").lower()


def _has_paid_signal(pay_text: str) -> bool:
    if re.search(r"(?<!бес)платн", pay_text):
        return True
    if "тариф" in pay_text or "₽" in pay_text or "paid" in pay_text:
        return True
    if "оплат" in pay_text and "бесплат" not in pay_text and "без оплат" not in pay_text:
        return True
    if re.search(r"\d+\s*руб", pay_text) or re.search(r"руб[./]", pay_text):
        return True
    if re.search(r"руб\w*\s*/?\s*(?:час|сут|мес|ден|нед)", pay_text):
        return True
    return False


def _is_underground_field(row: pd.Series) -> bool:
    if "level_count_2gis" in row.index:
        level = _safe_int(row.get("level_count_2gis"))
        if level is not None and level < 0:
            return True
    for column in ("purpose_2gis", "purpose_name_2gis"):
        if column in row.index and "подзем" in str(row.get(column) or "").lower():
            return True
    return False


def _classify_parking_type(row: pd.Series) -> tuple[str, str, bool, str, str, str, bool]:
    return "Исключена из расчёта", "Публичность не подтверждена", False, "Фильтр не применён", "Неизвестно", "base_not_patched", False


def _detect_excluded_owner(text: str) -> str | None:
    for owner_type, markers in EXCLUDED_OWNER_MARKERS.items():
        if any(_owner_marker_in_text(text, marker) for marker in markers):
            return owner_type
    return None


def _owner_marker_in_text(text: str, marker: str) -> bool:
    marker = marker.lower().replace("ё", "е")
    if " " in marker or "-" in marker:
        return marker in text
    if marker in EXCLUDED_OWNER_EXACT_TOKENS:
        return re.search(rf"(?<![а-яa-z0-9]){re.escape(marker)}(?![а-яa-z0-9])", text) is not None
    return re.search(rf"(?<![а-яa-z0-9]){re.escape(marker)}[а-яa-z]*", text) is not None


def _marker_in_text(text: str, marker: str) -> bool:
    marker = marker.lower().replace("ё", "е")
    if marker in {"тц", "трц", "трк", "бц"}:
        return re.search(rf"(?<![а-яa-z0-9]){re.escape(marker)}(?![а-яa-z0-9])", text) is not None
    return marker in text


def _extract_parking_spaces(row: pd.Series, *, parking_type: str) -> tuple[int | None, str, bool]:
    return None, "Нет точных данных 2GIS по количеству мест", False


def _first_positive_direct_capacity(row: pd.Series, *, skip_columns: set[str] | None = None) -> int | None:
    skip_columns = skip_columns or set()
    for column in DIRECT_CAPACITY_COLUMNS:
        if column in skip_columns or column not in row.index:
            continue
        parsed = _capacity_from_value(row.get(column))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _capacity_from_value(value: Any) -> int | None:
    if _is_missing(value):
        return None
    if isinstance(value, dict):
        for key in ("total", "count", "value", "capacity", "places", "spaces", "parking_spaces", "parking_places"):
            if key in value:
                parsed = _safe_int(value.get(key))
                if parsed is not None and parsed > 0:
                    return parsed
        return None
    if isinstance(value, (list, tuple)):
        values = [_capacity_from_value(item) for item in value]
        values = [item for item in values if item is not None and item > 0]
        return max(values) if values else None
    parsed = _safe_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _label_has_disabled_marker(label: str) -> bool:
    label = label.lower().replace("ё", "е")
    return any(marker in label for marker in DISABLED_PARKING_CAPACITY_MARKERS)


def _extract_capacity_from_text(text: str) -> int | None:
    if not text:
        return None
    patterns = [r"(?:вместимость|capacity)\D{0,30}(\d+)", r"(\d+)\s*(?:машино[-\s]?мест|машиномест)", r"(\d+)\s*парковочн\w*\s*мест", r"на\s*(\d+)\s*(?:автомобил|авто|машин|мест)"]
    candidates: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            context = text[max(0, match.start() - 35):min(len(text), match.end() + 35)]
            if _label_has_disabled_marker(context):
                continue
            candidates.append(int(match.group(1)))
    return max(candidates) if candidates else None


def _recursive_find_capacity(row: pd.Series) -> int | None:
    for column in ("attribute_groups", "raw_2gis"):
        if column in row.index:
            value = _recursive_find_int(row.get(column), DIRECT_CAPACITY_COLUMNS)
            if value is not None and value > 0:
                return value
    return None


def _is_capacity_label(label: str) -> bool:
    label = label.lower().replace("ё", "е")
    if _label_has_disabled_marker(label):
        return False
    return any(marker in label for marker in TOTAL_CAPACITY_LABEL_MARKERS)


def _attr_number(attribute_groups: Any, label_match: Callable[[str], bool]) -> int | None:
    for attr in _iter_attribute_dicts(attribute_groups):
        label = " ".join(str(attr.get(k, "")) for k in ("name", "tag", "alias", "id")).lower().replace("ё", "е")
        if not label_match(label):
            continue
        for value_key in ("value", "values", "count", "number"):
            parsed = _safe_int(attr.get(value_key))
            if parsed is not None and parsed > 0:
                return parsed
    return None


def _attr_number_for(row: pd.Series, label_match: Callable[[str], bool]) -> int | None:
    for source in ("attribute_groups", "raw_2gis"):
        if source in row.index:
            parsed = _attr_number(row.get(source), label_match)
            if parsed is not None:
                return parsed
    return None


def _iter_attribute_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_attribute_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_attribute_dicts(item)


def _spaces_from_area(row: pd.Series) -> int | None:
    hull = row.get("geometry_hull") if "geometry_hull" in row.index else None
    if _is_missing(hull):
        raw = row.get("raw_2gis") if "raw_2gis" in row.index else None
        if isinstance(raw, dict):
            hull = (raw.get("geometry") or {}).get("hull")
    area = _hull_area_m2(hull)
    if area is None or area < MIN_PARKING_AREA_M2:
        return None
    spaces = int(area // AREA_PER_PARKING_SPACE_M2)
    return spaces if spaces > 0 else None


def _hull_area_m2(hull: Any) -> float | None:
    geom = _parse_geometry(hull)
    if geom is None or geom.is_empty:
        return None
    try:
        latitude = float(geom.centroid.y)
        meters_per_deg_lat = 111_320.0
        meters_per_deg_lon = 111_320.0 * math.cos(math.radians(latitude))
        projected = shapely_transform(lambda x, y, z=None: (x * meters_per_deg_lon, y * meters_per_deg_lat), geom)
        area = float(projected.area)
        return area if area > 0 else None
    except Exception:
        return None


def _estimate_parking_spaces(row: pd.Series, parking_type: str) -> int:
    text = _row_text(row)
    if "многоуровнев" in text or "многоуровневая" in parking_type.lower():
        return 220
    if "подзем" in text:
        return 80
    if "платная" in text or "автостоян" in text:
        return 55
    if "паркинг" in text:
        return 60
    if "парковк" in text:
        return 35
    return 25


def _named_int_bounded(row: pd.Series, columns: list[str], lo: int, hi: int) -> int | None:
    for column in columns:
        if column not in row.index:
            continue
        value = _safe_int(row.get(column))
        if value is not None and lo <= value <= hi:
            return value
    return None


def _attr_int_bounded(row: pd.Series, predicate: Callable[[str], bool], lo: int, hi: int) -> int | None:
    if "attribute_groups" not in row.index:
        return None
    value = _attr_number(row.get("attribute_groups"), predicate)
    if value is not None and lo <= value <= hi:
        return value
    return None


def _is_flat_count_label(label: str) -> bool:
    return "квартир" in label and not any(token in label for token in ("цена", "стоимост", "руб", "млн", "ипотек", "продаж", "аренд", "от "))


def _desc_text(row: pd.Series) -> str:
    parts: list[str] = []
    for column in ("Название", "name", "description_2gis", "description"):
        if column not in row.index:
            continue
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return " ".join(parts).replace("ё", "е").lower()


def _extract_apartments(row: pd.Series) -> tuple[int, str, bool]:
    direct = _named_int_bounded(row, ["flat_count_2gis", "apartments", "apartment_count", "flats", "flat_count", "Квартир_всего", "Количество_квартир"], MIN_RELIABLE_APARTMENTS, MAX_PLAUSIBLE_APARTMENTS)
    if direct is not None:
        return direct, "2GIS: количество квартир из прямого поля", True
    attr_flats = _attr_int_bounded(row, _is_flat_count_label, MIN_RELIABLE_APARTMENTS, MAX_PLAUSIBLE_APARTMENTS)
    if attr_flats is not None:
        return attr_flats, "2GIS: количество квартир из атрибутов", True
    estimate, method = _estimate_apartments(row)
    return estimate, method, False


def _extract_floors(row: pd.Series) -> int | None:
    floors = _named_int_bounded(row, ["floors_2gis", "floors", "floor_count", "building_levels", "storeys", "Этажность", "Этажей"], 1, MAX_FLOORS)
    if floors is not None:
        return floors
    attr_floors = _attr_int_bounded(row, lambda label: "этаж" in label and "квартир" not in label and "цена" not in label, 1, MAX_FLOORS)
    if attr_floors is not None:
        return attr_floors
    match = re.search(r"(\d+)\s*[-\s]?этажн", _desc_text(row))
    if match:
        value = int(match.group(1))
        if 1 <= value <= MAX_FLOORS:
            return value
    return None


def _estimate_apartments(row: pd.Series) -> tuple[int, str]:
    floors = _extract_floors(row)
    entrances = _extract_entrances(row)
    floors_val = floors if floors else DEFAULT_FLOORS
    entrances_val = entrances if entrances else DEFAULT_ENTRANCES
    apartments = int(floors_val * entrances_val * DEFAULT_FLATS_PER_FLOOR_PER_ENTRANCE)
    apartments = min(max(apartments, DEFAULT_APARTMENTS_PER_BUILDING), MAX_PLAUSIBLE_APARTMENTS)
    return apartments, f"Оценка: {floors_val} эт. × {entrances_val} подъезд. × {DEFAULT_FLATS_PER_FLOOR_PER_ENTRANCE} кв./этаж = {apartments}"


def _extract_entrances(row: pd.Series) -> int | None:
    entrances = _named_int_bounded(row, ["entrance_count_2gis", "entrance_count", "entrances_count", "entrances", "Количество_подъездов", "Подъездов"], 1, MAX_ENTRANCES)
    if entrances is not None:
        return entrances
    return _attr_int_bounded(row, lambda label: "подъезд" in label and "квартир" not in label, 1, MAX_ENTRANCES)


def _sum_spaces(df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    return int(pd.to_numeric(df.get("Парковочных_мест", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())


def _zone_weight(zone_label: str) -> float:
    if zone_label == "0–5 минут":
        return ZONE_WEIGHT_0_5
    if zone_label == "5–10 минут":
        return ZONE_WEIGHT_5_10
    return 1.0


def _parking_type_weight(parking_type: str) -> float:
    if parking_type == "Бесплатная":
        return FREE_PARKING_WEIGHT
    if parking_type == "Платная":
        return PAID_PARKING_WEIGHT
    if parking_type == "Неизвестно":
        return UNKNOWN_PARKING_WEIGHT
    return 0.0


def _weighted_spaces_for_zone(parkings: pd.DataFrame, zone_label: str) -> float:
    if parkings is None or parkings.empty:
        return 0.0
    total = 0.0
    for _, row in parkings.iterrows():
        spaces = _safe_float(row.get("Парковочных_мест"), 0.0)
        p_type = str(row.get("Тип_парковки") or "Неизвестно")
        row_zone = str(row.get("Зона") or zone_label)
        zone_coef = _zone_weight(row_zone) if zone_label == "Итого до 10 минут" else _zone_weight(zone_label)
        total += spaces * _parking_type_weight(p_type) * zone_coef
    return total


def _clip_score(value: float) -> float:
    try:
        if math.isnan(value):
            return math.nan
    except TypeError:
        return math.nan
    return max(0.0, min(10.0, float(value)))


def _classify_parking_potential(score: float | None) -> str:
    if score is None:
        return "Нет данных"
    try:
        if math.isnan(float(score)):
            return "Нет данных"
    except (TypeError, ValueError):
        return "Нет данных"
    score = float(score)
    if score >= 8:
        return "Высокий"
    if score >= 4:
        return "Средний"
    return "Низкий"


def _build_parking_details(poi_df: pd.DataFrame, iso_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(columns=_parking_detail_columns())


def _build_residential_details(poi_df: pd.DataFrame, iso_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(columns=["Адрес", "Количество_подъездов", "Квартир_всего", "Метод_расчёта", "Данные_по_квартирам", "Зона", "Минут_пешком", "dgis_id"])


def _build_parking_summary(residential_df: pd.DataFrame, parking_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(columns=_summary_columns())


def _build_text_outputs(summary_df: pd.DataFrame) -> tuple[str, str]:
    return "Парковочная обеспеченность не оценена: недостаточно данных 2GIS.", "Парковка: нет данных"


def _empty_result(text: str) -> ParkingSupplyResult:
    return ParkingSupplyResult(pd.DataFrame(columns=_summary_columns()), pd.DataFrame(columns=_parking_detail_columns()), pd.DataFrame(), text, "Парковка: нет данных")


def _parking_detail_columns() -> list[str]:
    return ["Название", "Адрес", "Категория_2GIS", "Тип_парковки", "Доступность", "Парковочных_мест", "Учитывается_в_расчёте", "Причина_исключения", "Зона", "Минут_пешком", "dgis_id", "Метод_расчёта_мест", "Данные_по_местам", "Можно_купить_место", "Тип_связанного_объекта", "Логика_фильтрации"]


def _summary_columns() -> list[str]:
    return ["Зона", "Минут_пешком", "Жилых_домов", "Домов_с_данными_по_квартирам", "Квартир_в_зоне", "Парковочных_объектов", "Парковочных_мест", "Бесплатных_мест", "Платных_мест", "Мест_с_неизвестным_типом", "Исключённых_парковок", "Взвешенных_парковочных_мест", "Парковочный_коэффициент", "Оценка_из_10", "Класс_обеспеченности", "Комментарий", "Коэффициент_владения_авто", "Расчётная_потребность_машиномест", "Взвешенный_коэффициент_мест_на_квартиру"]


def _row_text(row: pd.Series) -> str:
    parts: list[str] = []
    for value in row.values:
        if _is_missing(value):
            continue
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
        elif isinstance(value, (dict, list, tuple, set)):
            parts.append(json.dumps(value, ensure_ascii=False, default=str))
    return " ".join(parts).replace("ё", "е").lower()


def _pick(row: pd.Series, columns: list[str]) -> Any:
    for column in columns:
        if column not in row.index:
            continue
        value = row.get(column)
        if _is_missing(value):
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        return value
    return pd.NA


def _recursive_find_int(value: Any, markers: list[str]) -> int | None:
    marker_texts = {m.lower() for m in markers}
    key_positive_markers = {"квартир", "этаж", "подъезд", "capacity", "spaces", "parking_spaces", "parking_places", "total_places", "places_total", "total_capacity", "вместим", "машино", "парковочных мест", "количество мест"}
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower().replace("ё", "е")
            if _label_has_disabled_marker(key_text):
                continue
            if key_text in marker_texts or any(marker in key_text for marker in key_positive_markers):
                parsed = _safe_int(item)
                if parsed is not None:
                    return parsed
            parsed = _recursive_find_int(item, markers)
            if parsed is not None:
                return parsed
    elif isinstance(value, list):
        for item in value:
            parsed = _recursive_find_int(item, markers)
            if parsed is not None:
                return parsed
    elif isinstance(value, str):
        text = value.lower().replace("ё", "е")
        if _label_has_disabled_marker(text):
            return None
        if any(marker in text for marker in ["квартир", "машино", "парковоч", "этаж", "подъезд"]):
            match = re.search(r"(\d+)", text)
            if match:
                return int(match.group(1))
    return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False


def _safe_int(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        match = re.search(r"(\d+)", str(value))
        return int(match.group(1)) if match else None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_existing_column(data: pd.DataFrame, columns: list[str]) -> str | None:
    for column in columns:
        if column in data.columns:
            return column
    return None
