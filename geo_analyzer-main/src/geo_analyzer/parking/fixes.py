from __future__ import annotations

"""Strict 2GIS API-only parking potential contract.

The parking potential module must not create house or parking candidates from text
queries or generic POI names. Candidates are allowed only from structured 2GIS
objects:

- /3.0/items with type=parking;
- /3.0/items with type=building;
- /3.0/items with id=... for the detailed card used to read capacity, floors,
  entrances and flats.

The old core module is intentionally patched at runtime because several legacy
imports still point to ``geo_analyzer.parking.supply`` and
``geo_analyzer.metrics.parking_supply``.
"""

import hashlib
import importlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd

_supply = importlib.import_module("geo_analyzer.parking.supply")

PARKING_LOADER_VERSION = "parking_v26_type_only_verified_counts"
_supply.PARKING_LOADER_VERSION = PARKING_LOADER_VERSION

ParkingSupplyResult = _supply.ParkingSupplyResult
CAR_OWNERSHIP_COEF = _supply.CAR_OWNERSHIP_COEF
DEFAULT_APARTMENTS_PER_BUILDING = _supply.DEFAULT_APARTMENTS_PER_BUILDING
BUYABLE_SPACE_MARKERS = _supply.BUYABLE_SPACE_MARKERS
CLOSED_PARKING_MARKERS = _supply.CLOSED_PARKING_MARKERS

API_PAGE_SLEEP_SEC = 0.12
DETAIL_BATCH_SIZE = 50
BUILDING_GRID_FACTOR = 0.45

EXTRA_NON_RESIDENTIAL_BUILDING_MARKERS = (
    "павильон",
    "киоск",
    "ларек",
    "ларёк",
    "котельн",
    "бойлерн",
    "теплопункт",
    "тепловой пункт",
    "трансформатор",
    "электроподстанц",
    "подстанц",
    "тп ",
    "цтп",
    "насосная",
    "градирн",
    "автомой",
    "шиномонтаж",
    "автосервис",
    "сто ",
    "пост охраны",
    "проходная",
    "административное здание",
    "нежилое здание",
    "коммерческое здание",
    "торговый павильон",
    "складское помещение",
    "гаражный бокс",
    "гск",
    "блок гаражей",
)

RESIDENTIAL_SIGNAL_MARKERS = (
    "жилой дом",
    "жилые дома",
    "многоквартир",
    "residential",
    "apartment",
)

RESIDENTIAL_PROJECT_CARD_MARKERS = (
    "жилой комплекс",
    "жк ",
    " жк",
    "новострой",
    "строящийся",
    "строящ",
    "офис продаж",
    "отдел продаж",
)

PARKING_FALSE_POSITIVE_MARKERS = (
    "ремонт парковочного оборудования",
    "оборудование для парковки",
    "шлагбаумы",
    "автосервис",
    "сервисный центр",
    "строительная компания",
    "строительство бассейнов",
    "аквапарков",
)

DROP_OFF_MARKERS = (
    "место высадки",
    "места высадки",
    "зона высадки",
    "посадка-высадка",
    "посадка и высадка",
    "kiss and ride",
    "drop off",
    "drop-off",
)

PUBLIC_ACCESS_MARKERS = (
    "общедоступ",
    "общественная",
    "общественный",
    "публичная",
    "публичный",
    "public",
    "free access",
)

CITY_PUBLIC_MARKERS = (
    "городская",
    "городской",
    "муниципальная",
    "муниципальный",
    "уличная",
    "уличный",
    "public city",
    "municipal",
    "street parking",
)

GENERIC_PARKING_NAMES = {
    "парковка",
    "парковки",
    "паркинг",
    "паркинги",
    "автостоянка",
    "автостоянки",
    "стоянка",
    "стоянки",
    "parking",
    "parking lot",
}

FLAT_COUNT_LABEL_MARKERS = (
    "квартир",
    "квартира",
    "flat_count",
    "flats_count",
    "flats",
    "apartments",
    "apartment_count",
)

FLAT_COUNT_BAD_CONTEXT = (
    "цена",
    "стоимост",
    "руб",
    "млн",
    "ипотек",
    "продаж",
    "аренд",
    "от ",
    "м2",
    "м²",
    "площад",
    "комнат",
)

PARKING_CAPACITY_FIELDS = (
    "items.id,items.external_id,items.point,items.geometry.hull,items.address_name,"
    "items.full_address_name,items.rubrics,items.name,items.attribute_groups,items.schedule,"
    "items.context,items.access,items.access_comment,items.capacity,items.is_paid,"
    "items.for_trucks,items.paving_type,items.is_incentive,items.purpose,"
    "items.purpose_name,items.level_count,items.links,items.description,items.statistics"
)

RESIDENTIAL_COUNT_FIELDS = (
    "items.id,items.external_id,items.point,items.geometry.hull,items.address_name,"
    "items.full_address_name,items.rubrics,items.name,items.attribute_groups,"
    "items.description,items.links,items.statistics,"
    "items.floors,items.floor_count,items.storeys,"
    "items.flat_count,items.flats,items.apartments,"
    "items.entrance_count,items.entrances,items.purpose,items.purpose_name"
)


def _norm(value: Any) -> str:
    return str(value or "").replace("ё", "е").lower().strip()


def _value_to_text(value: Any) -> str:
    if _supply._is_missing(value):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple, set, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _row_field_text(row: pd.Series, columns: tuple[str, ...]) -> str:
    parts: list[str] = []
    for column in columns:
        if column not in row.index:
            continue
        text = _value_to_text(row.get(column)).strip()
        if text:
            parts.append(text)
    return _norm(" ".join(parts))


def _classifier_category_text(row: pd.Series) -> str:
    return _row_field_text(
        row,
        (
            "Категория_2GIS",
            "Категория_2GIS_официальная",
            "rubric_name",
            "category",
            "Категория",
            "rubrics_2gis",
            "source_categories_2gis",
            "purpose_2gis",
            "purpose_name_2gis",
            "Поисковый_запрос",
            "rubric_id",
        ),
    )


def _classifier_name_text(row: pd.Series) -> str:
    return _row_field_text(row, ("Название", "name"))


def _classifier_address_text(row: pd.Series) -> str:
    return _row_field_text(row, ("Адрес", "address", "address_name", "full_address_name"))


def _residential_classifier_text(row: pd.Series) -> str:
    return _row_field_text(
        row,
        (
            "Название",
            "name",
            "Адрес",
            "address",
            "address_name",
            "full_address_name",
            "Категория_2GIS",
            "Категория_2GIS_официальная",
            "rubric_name",
            "purpose_2gis",
            "purpose_name_2gis",
            "rubrics_2gis",
            "source_categories_2gis",
            "Поисковый_запрос",
            "rubric_id",
        ),
    )


def _parking_classifier_text(row: pd.Series) -> str:
    return _row_field_text(
        row,
        (
            "Название",
            "name",
            "Категория_2GIS",
            "Категория_2GIS_официальная",
            "rubric_name",
            "category",
            "Категория",
            "purpose_2gis",
            "purpose_name_2gis",
            "rubrics_2gis",
            "source_categories_2gis",
            "Поисковый_запрос",
            "rubric_id",
        ),
    )


def _type_marker(row: pd.Series) -> str:
    return " ".join(_norm(row.get(c)) for c in ("Поисковый_запрос", "rubric_id") if c in row.index)


def _is_type_row(row: pd.Series, kind: str, marker: str) -> bool:
    actual_kind = _norm(row.get("_semantic_kind")) if "_semantic_kind" in row.index else ""
    return actual_kind == kind and marker in _type_marker(row)


def _building_item_text(item: dict[str, Any]) -> str:
    parts = [
        _value_to_text(item.get("purpose")),
        _value_to_text(item.get("purpose_name")),
        _value_to_text(item.get("name")),
        _value_to_text(item.get("address_name")),
        _value_to_text(item.get("full_address_name")),
    ]
    for rubric in item.get("rubrics") or []:
        if isinstance(rubric, dict):
            parts.append(_value_to_text(rubric.get("name")))
            parts.append(_value_to_text(rubric.get("caption")))
            parts.append(_value_to_text(rubric.get("display_name")))
    return _norm(" ".join(part for part in parts if part))


def _has_non_residential_building_signal(text: str) -> bool:
    markers = (
        tuple(_supply.NON_RESIDENTIAL_PURPOSE_MARKERS)
        + tuple(_supply.NON_RESIDENTIAL_BUILDING_NAME_MARKERS)
        + EXTRA_NON_RESIDENTIAL_BUILDING_MARKERS
    )
    return any(_norm(marker) in text for marker in markers if _norm(marker))


def _has_residential_signal(text: str) -> bool:
    markers = tuple(getattr(_supply, "RESIDENTIAL_PURPOSE_MARKERS", ())) + RESIDENTIAL_SIGNAL_MARKERS
    return any(_norm(marker) in text for marker in markers if _norm(marker))


def _has_residential_project_card_signal(text: str) -> bool:
    return any(_norm(marker) in text for marker in RESIDENTIAL_PROJECT_CARD_MARKERS)


def _has_drop_off_signal(text: str) -> bool:
    return any(_norm(marker) in text for marker in DROP_OFF_MARKERS)


def _looks_like_address(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"\b\d+[а-яa-z]?\b", text)) and any(
        marker in text
        for marker in (
            "улица",
            "ул ",
            "ул.",
            "проспект",
            "пр-кт",
            "переулок",
            "пер ",
            "шоссе",
            "бульвар",
            "набережная",
            "проезд",
        )
    )


def _cache_path_api_contract(latitude: float, longitude: float, radius_m: int) -> Path:
    settings = _supply.get_settings()
    payload = {
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
        "radius_m": int(radius_m),
        "version": PARKING_LOADER_VERSION,
        "source": "2gis_type_parking_type_building_detail_cards_only",
    }
    digest = hashlib.md5(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return settings.cache_dir / "parking_supply" / f"{digest}.json"


def _building_is_residential_fixed(item: dict[str, Any]) -> bool:
    # First-pass filter for raw 2GIS type=building response. It is intentionally
    # permissive because detailed purpose/flats/floors can be present only in
    # the follow-up object card. The final row filter runs after enrichment.
    text = _building_item_text(item)
    if not text:
        return True
    if _has_non_residential_building_signal(text):
        return False
    if _has_residential_project_card_signal(text):
        return False
    if _has_residential_signal(text):
        return True
    if _looks_like_address(text):
        return True
    return True


def _is_residential_building_fixed(row: pd.Series) -> bool:
    if not _is_type_row(row, "residential", "type:building"):
        return False
    text = _residential_classifier_text(row)
    if _has_non_residential_building_signal(text):
        return False
    if _has_residential_project_card_signal(text):
        return False
    return True


def _is_parking_object_fixed(row: pd.Series) -> bool:
    if not _is_type_row(row, "parking", "type:parking"):
        return False
    text = _parking_classifier_text(row)
    if any(_norm(marker) in text for marker in PARKING_FALSE_POSITIVE_MARKERS):
        return False
    if _has_drop_off_signal(text):
        return False
    return True


def _flat_completeness_score(row: pd.Series) -> int:
    score = 0
    for column in ("flat_count_2gis", "apartments", "apartment_count", "flats", "flat_count", "Квартир_всего", "Количество_квартир"):
        if column in row.index and _supply._safe_int(row.get(column)) is not None:
            score += 6
            break
    for column in ("entrance_count_2gis", "entrance_count", "entrances_count", "entrances", "Количество_подъездов", "Подъездов"):
        if column in row.index and _supply._safe_int(row.get(column)) is not None:
            score += 3
            break
    for column in ("floors_2gis", "floors", "floor_count", "building_levels", "storeys", "Этажность", "Этажей"):
        if column in row.index and _supply._safe_int(row.get(column)) is not None:
            score += 2
            break
    if "attribute_groups" in row.index and not _supply._is_missing(row.get("attribute_groups")):
        score += 1
    if row.get("residential_card_checked_2gis") is True or row.get("parking_capacity_checked_2gis") is True:
        score += 1
    return score


def _dedupe_priority(row: pd.Series) -> int:
    if _is_type_row(row, "parking", "type:parking"):
        return 0
    if _is_type_row(row, "residential", "type:building"):
        return 1
    return 9


def _deduplicate_fixed(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    data = df.copy()
    for column in ["dgis_id", "fid", "Название", "Адрес", "Широта", "Долгота", "_semantic_kind", "Поисковый_запрос"]:
        if column not in data.columns:
            data[column] = pd.NA

    dgis_key = data["dgis_id"].fillna("").astype(str).str.strip()
    kind_key = data["_semantic_kind"].fillna("").astype(str).str.strip().str.lower()
    fallback = (
        data["Название"].fillna("").astype(str).str.lower().str.strip()
        + "|"
        + data["Адрес"].fillna("").astype(str).str.lower().str.strip()
        + "|"
        + data["Широта"].fillna("").astype(str).str.strip()
        + "|"
        + data["Долгота"].fillna("").astype(str).str.strip()
    )
    data["_dedupe_key"] = (kind_key + "|" + dgis_key).where(dgis_key.ne(""), kind_key + "|" + fallback)
    data["_dedupe_priority"] = data.apply(_dedupe_priority, axis=1)
    data["_flat_completeness"] = data.apply(_flat_completeness_score, axis=1)
    data = data.sort_values(
        ["_dedupe_key", "_dedupe_priority", "_flat_completeness", "Название"],
        ascending=[True, True, False, True],
        na_position="last",
    )
    return (
        data.drop_duplicates(subset=["_dedupe_key"], keep="first")
        .drop(columns=["_dedupe_key", "_dedupe_priority", "_flat_completeness"], errors="ignore")
        .reset_index(drop=True)
    )


def _is_flat_count_label_fixed(label: str) -> bool:
    text = _norm(label)
    if not any(marker in text for marker in FLAT_COUNT_LABEL_MARKERS):
        return False
    return not any(marker in text for marker in FLAT_COUNT_BAD_CONTEXT)


def _bool_from_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "да", "y"}
    return bool(value)


def _row_flag(row: pd.Series, column: str) -> bool:
    return column in row.index and _bool_from_value(row.get(column))


def _extract_apartments_fixed(row: pd.Series) -> tuple[int, str, bool]:
    direct = _supply._named_int_bounded(
        row,
        [
            "flat_count_2gis",
            "apartments",
            "apartment_count",
            "apartments_count",
            "flats",
            "flats_count",
            "flat_count",
            "Квартир_всего",
            "Количество_квартир",
        ],
        _supply.MIN_RELIABLE_APARTMENTS,
        _supply.MAX_PLAUSIBLE_APARTMENTS,
    )
    if direct is not None:
        return direct, "2GIS: количество квартир из прямого поля", True

    attr_flats = _supply._attr_int_bounded(
        row,
        _is_flat_count_label_fixed,
        _supply.MIN_RELIABLE_APARTMENTS,
        _supply.MAX_PLAUSIBLE_APARTMENTS,
    )
    if attr_flats is not None:
        return attr_flats, "2GIS: количество квартир из атрибутов", True

    entrances = _supply._extract_entrances(row)
    flats_per_entrance = _supply._named_int_bounded(
        row,
        ["apartments_per_entrance", "flats_per_entrance", "Квартир_в_подъезде"],
        1,
        _supply.MAX_FLATS_PER_ENTRANCE,
    )
    if not flats_per_entrance:
        flats_per_entrance = _supply._attr_int_bounded(
            row,
            lambda label: "квартир" in _norm(label) and "подъезд" in _norm(label),
            1,
            _supply.MAX_FLATS_PER_ENTRANCE,
        )

    if entrances and flats_per_entrance:
        total = entrances * flats_per_entrance
        if _supply.MIN_RELIABLE_APARTMENTS <= total <= _supply.MAX_PLAUSIBLE_APARTMENTS:
            return total, "2GIS: подъезды × квартир в подъезде", True

    desc_text = _supply._desc_text(row)
    for pattern in [r"(\d+)\s*(?:квартир|кв\.)", r"квартир\s*[:\-]?\s*(\d+)"]:
        match = re.search(pattern, desc_text)
        if not match:
            continue
        value = int(match.group(1))
        if _supply.MIN_RELIABLE_APARTMENTS <= value <= _supply.MAX_PLAUSIBLE_APARTMENTS:
            return value, "2GIS: извлечено из описания", True

    if _row_flag(row, "residential_card_checked_2gis"):
        estimate, method = _estimate_apartments_fixed(row)
        return estimate, method, False

    return 0, "Нет точного количества квартир; оценка запрещена без подтверждённой проверки карточки 2GIS", False


def _estimate_apartments_fixed(row: pd.Series) -> tuple[int, str]:
    floors = _supply._extract_floors(row)
    entrances = _supply._extract_entrances(row)
    floors_val = floors if floors else _supply.DEFAULT_FLOORS
    entrances_val = entrances if entrances else _supply.DEFAULT_ENTRANCES
    apartments = int(floors_val * entrances_val * _supply.DEFAULT_FLATS_PER_FLOOR_PER_ENTRANCE)
    apartments = max(apartments, _supply.DEFAULT_APARTMENTS_PER_BUILDING)
    apartments = min(apartments, _supply.MAX_PLAUSIBLE_APARTMENTS)

    src_floors = "2GIS" if floors else "дефолт после проверки 2GIS"
    src_entrances = "2GIS" if entrances else "дефолт после проверки 2GIS"
    method = (
        f"Оценка после проверки карточки 2GIS: {floors_val} эт. ({src_floors}) × "
        f"{entrances_val} подъезд. ({src_entrances}) × {_supply.DEFAULT_FLATS_PER_FLOOR_PER_ENTRANCE} кв./этаж = {apartments}"
    )
    return apartments, method


def _normalize_access_text(row: pd.Series) -> str:
    raw = " ".join(str(row.get(col, "") or "") for col in ("access_2gis", "access_comment_2gis"))
    try:
        return _supply.normalize_category_name(raw)
    except Exception:
        return _norm(raw)


def _parking_identity_text(row: pd.Series) -> str:
    return " ".join(
        [
            _classifier_name_text(row),
            _classifier_address_text(row),
            _classifier_category_text(row),
            _row_field_text(row, ("description_2gis", "description")),
        ]
    )


def _has_public_access(row: pd.Series) -> bool:
    text = " ".join([_normalize_access_text(row), _parking_identity_text(row)])
    return any(marker in text for marker in PUBLIC_ACCESS_MARKERS + CITY_PUBLIC_MARKERS)


def _has_city_public_signal(row: pd.Series) -> bool:
    # Intentionally excludes access_2gis="Общедоступная". For generic unnamed
    # 2GIS parking objects this field only means the object is visible, not that
    # it is a valid parking resource for residential demand.
    text = _parking_identity_text(row)
    return any(marker in text for marker in CITY_PUBLIC_MARKERS)


def _is_generic_parking_record(row: pd.Series) -> bool:
    name = _classifier_name_text(row)
    address = _classifier_address_text(row)
    if address:
        return False
    if not name:
        return True
    normalized_name = re.sub(r"\s+", " ", name).strip(" .,:;")
    return normalized_name in GENERIC_PARKING_NAMES


def _classify_parking_type_fixed(row: pd.Series) -> tuple[str, str, bool, str, str, str, bool]:
    text = _supply._row_text(row)
    narrow_text = _parking_classifier_text(row)
    pay_text = _supply._pay_text(row)
    owner_text = _supply._narrow_owner_text(row)
    hard_residential_text = " ".join([narrow_text, _normalize_access_text(row)])
    buyable = _supply._can_buy_space(text)
    is_paid = _supply._is_paid_field(row)

    explicit_paid = is_paid is True or _supply._has_paid_signal(pay_text)
    explicit_free = any(marker in pay_text for marker in _supply.FREE_PARKING_MARKERS)
    explicit_public = _has_public_access(row)
    city_public = _has_city_public_signal(row)
    generic_record = _is_generic_parking_record(row)

    is_restricted = any(_supply._marker_in_text(text, marker) for marker in _supply.CLOSED_PARKING_MARKERS)
    is_underground = _supply._is_underground_field(row) or any(_supply._marker_in_text(text, marker) for marker in _supply.UNDERGROUND_MARKERS)

    if _has_drop_off_signal(narrow_text):
        return "Исключена из расчёта", "Место высадки", False, "Место посадки/высадки не является парковочным ресурсом", "Drop-off зона", "excluded_dropoff", buyable

    if any(_supply._marker_in_text(text, marker) for marker in _supply.RESTRICTED_NON_RESIDENTIAL_MARKERS):
        return "Исключена из расчёта", "Для сотрудников", False, "Служебная/корпоративная парковка для сотрудников", "Служебная парковка", "excluded_staff_or_service", buyable

    if any(_supply._marker_in_text(hard_residential_text, marker) for marker in _supply.HARD_RESIDENTIAL_PARKING_MARKERS):
        return "Исключена из расчёта", "Для жителей/резидентов", False, "Парковка только для жильцов/резидентов здания", "Жилой объект или дворовая территория", "excluded_residential_private", buyable

    owner_type = _supply._detect_excluded_owner(owner_text)
    if owner_type and not city_public:
        return "Исключена из расчёта", "Для посетителей/сотрудников объекта", False, f"Парковка относится к объекту категории: {owner_type}", owner_type, "excluded_non_residential_owner", buyable

    if is_underground and not (explicit_public and explicit_paid):
        return "Исключена из расчёта", "Подземный паркинг", False, "Подземный паркинг не подтверждён как общедоступный городской ресурс", "Подземный паркинг", "excluded_underground_non_public", buyable

    if any(_supply._marker_in_text(hard_residential_text, marker) for marker in _supply.RESIDENTIAL_PARKING_MARKERS) and not city_public and not buyable:
        return "Исключена из расчёта", "Для жителей/жилой территории", False, "Парковка только для жильцов / дворовая закрытая парковка", "Жилой объект или дворовая территория", "excluded_residential_private", buyable

    if is_restricted and not city_public and not buyable:
        return "Исключена из расчёта", "Закрытый/ограниченный доступ", False, "Закрытая/приватная парковка без публичного доступа", "Закрытая парковка", "excluded_closed_private", buyable

    if generic_record and not explicit_paid and not buyable and not city_public:
        return "Исключена из расчёта", "Публичность не подтверждена", False, "Типовой объект «Парковка» без адреса, названия и городского/муниципального признака", "Не подтверждена общедоступность", "excluded_generic_no_public_evidence", buyable

    if explicit_paid:
        return "Платная", "Общедоступная", True, "", "2GIS: платная парковка", "included_paid", buyable

    if buyable:
        return "Неизвестно", "Доступно по покупке/аренде", True, "", "2GIS: покупка/аренда машиноместа", "included_buyable", buyable

    if explicit_free and (explicit_public or city_public):
        return "Бесплатная", "Общедоступная", True, "", "2GIS: бесплатная общедоступная парковка", "included_free_public", buyable

    if explicit_public and not generic_record:
        return "Неизвестно", "Общедоступная", True, "", "2GIS: общедоступная парковка без данных об оплате", "included_public_unknown_payment", buyable

    return "Исключена из расчёта", "Публичность не подтверждена", False, "Нет подтверждения, что парковка общедоступна для жителей", "Не подтверждена общедоступность", "excluded_no_public_evidence", buyable


def _capacity_was_checked(row: pd.Series) -> bool:
    return _row_flag(row, "parking_capacity_checked_2gis")


def _extract_parking_spaces_fixed(row: pd.Series, *, parking_type: str) -> tuple[int | None, str, bool]:
    if "capacity_2gis" in row.index:
        capacity = _supply._capacity_from_value(row.get("capacity_2gis"))
        if capacity is not None and capacity > 0:
            return capacity, "2GIS: поле capacity", True

    direct = _supply._first_positive_direct_capacity(row, skip_columns={"capacity_2gis"})
    if direct is not None and direct > 0:
        return direct, "2GIS: прямое количество мест", True

    attr_capacity = _supply._attr_number_for(row, _supply._is_capacity_label)
    if attr_capacity is not None and attr_capacity > 0:
        return attr_capacity, "2GIS: количество мест из атрибутов", True

    recursive_capacity = _supply._recursive_find_capacity(row)
    if recursive_capacity is not None and recursive_capacity > 0:
        return recursive_capacity, "2GIS: количество мест из вложенных полей", True

    text_capacity = _supply._extract_capacity_from_text(_supply._row_text(row))
    if text_capacity is not None and text_capacity > 0:
        return text_capacity, "2GIS: извлечено из текста/атрибутов", True

    if _capacity_was_checked(row):
        area_spaces = _supply._spaces_from_area(row)
        if area_spaces is not None and area_spaces > 0:
            return area_spaces, "Оценка после проверки 2GIS: capacity отсутствует, расчёт по площади контура", False
        estimate = _supply._estimate_parking_spaces(row, parking_type)
        return estimate, "Оценка после проверки 2GIS: capacity отсутствует, типовая вместимость", False

    return None, "Нет точных данных 2GIS по количеству мест; оценка не применялась без проверки карточки", False


def _build_parking_details_fixed(poi_df: pd.DataFrame, iso_df: pd.DataFrame) -> pd.DataFrame:
    parking = poi_df[poi_df.apply(_is_parking_object_fixed, axis=1)].copy()
    rows: list[dict[str, Any]] = []

    for _, item in parking.iterrows():
        zone_label, minutes = _supply._detect_zone(item.get("geometry"), iso_df, max_minutes=10)
        if not zone_label:
            continue

        parking_type, availability, included, reason, owner_type, filter_rule, buyable = _classify_parking_type_fixed(item)
        spaces, spaces_method, has_exact_spaces = _extract_parking_spaces_fixed(item, parking_type=parking_type)

        rows.append(
            {
                "Название": _supply._pick(item, ["name", "Название"]),
                "Адрес": _supply._pick(item, ["address_name", "address", "Адрес"]),
                "Категория_2GIS": _supply._pick(item, ["Категория_2GIS_официальная", "rubric_name", "category", "Категория", "Категория_2GIS"]),
                "Тип_парковки": parking_type,
                "Доступность": availability,
                "Парковочных_мест": spaces if spaces is not None else 0,
                "Метод_расчёта_мест": spaces_method,
                "Данные_по_местам": "Да" if has_exact_spaces else "Нет",
                "Оценочное_значение": "Нет" if has_exact_spaces else ("Да" if spaces else "Нет"),
                "Проверка_вместимости_2GIS": "Да" if _capacity_was_checked(item) else "Нет",
                "Учитывается_в_расчёте": "Да" if included else "Нет",
                "Причина_исключения": reason,
                "Зона": zone_label,
                "Минут_пешком": minutes,
                "dgis_id": _supply._pick(item, ["id", "dgis_id", "ДГИС_ID"]),
                "Можно_купить_место": "Да" if buyable else "Нет",
                "Тип_связанного_объекта": owner_type,
                "Логика_фильтрации": filter_rule,
            }
        )

    return pd.DataFrame(rows, columns=_parking_detail_columns_fixed())


def _build_residential_details_fixed(poi_df: pd.DataFrame, iso_df: pd.DataFrame) -> pd.DataFrame:
    residential = poi_df[poi_df.apply(_is_residential_building_fixed, axis=1)].copy()
    rows: list[dict[str, Any]] = []

    for _, house in residential.iterrows():
        zone_label, minutes = _supply._detect_zone(house.get("geometry"), iso_df, max_minutes=10)
        if not zone_label:
            continue

        apartments, method, has_exact_data = _extract_apartments_fixed(house)
        entrances = _supply._extract_entrances(house)
        floors = _supply._extract_floors(house)

        rows.append(
            {
                "Адрес": _supply._pick(house, ["address_name", "address", "Адрес", "Название"]),
                "Количество_подъездов": entrances if entrances is not None else pd.NA,
                "Этажей": floors if floors is not None else pd.NA,
                "Квартир_всего": apartments if apartments is not None else pd.NA,
                "Метод_расчёта": method,
                "Данные_по_квартирам": "Да" if has_exact_data else "Нет",
                "Проверка_карточки_2GIS": "Да" if _row_flag(house, "residential_card_checked_2gis") else "Нет",
                "Зона": zone_label,
                "Минут_пешком": minutes,
                "dgis_id": _supply._pick(house, ["id", "dgis_id", "ДГИС_ID"]),
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "Адрес",
            "Количество_подъездов",
            "Этажей",
            "Квартир_всего",
            "Метод_расчёта",
            "Данные_по_квартирам",
            "Проверка_карточки_2GIS",
            "Зона",
            "Минут_пешком",
            "dgis_id",
        ],
    )


def _parking_detail_columns_fixed() -> list[str]:
    return [
        "Название",
        "Адрес",
        "Категория_2GIS",
        "Тип_парковки",
        "Доступность",
        "Парковочных_мест",
        "Метод_расчёта_мест",
        "Данные_по_местам",
        "Оценочное_значение",
        "Проверка_вместимости_2GIS",
        "Учитывается_в_расчёте",
        "Причина_исключения",
        "Зона",
        "Минут_пешком",
        "dgis_id",
        "Можно_купить_место",
        "Тип_связанного_объекта",
        "Логика_фильтрации",
    ]


def _included_buyable_spaces(parking_df: pd.DataFrame, zones: list[str]) -> int:
    if parking_df is None or parking_df.empty:
        return 0
    data = parking_df[parking_df["Зона"].isin(zones)].copy() if "Зона" in parking_df.columns else pd.DataFrame()
    if data.empty:
        return 0
    if "Учитывается_в_расчёте" in data.columns:
        data = data[data["Учитывается_в_расчёте"].astype(str).eq("Да")]
    if "Можно_купить_место" in data.columns:
        data = data[data["Можно_купить_место"].astype(str).eq("Да")]
    return _supply._sum_spaces(data)


def _build_comment_fixed(
    apartments: int,
    total_spaces: int,
    score: float | None,
    included_parking_objects: int,
    excluded_parkings: int,
    weighted_spaces: float,
    exact_spaces: int,
    estimated_spaces: int,
    houses: int,
    houses_with_exact_flats: int,
) -> str:
    if houses <= 0:
        return "Оценка не рассчитана: в зоне не обнаружено физических жилых домов из 2GIS type=building."
    if apartments <= 0:
        return "Оценка не рассчитана: 2GIS не подтвердил количество квартир и расчётную оценку квартир по карточкам зданий."
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return "Оценка не рассчитана: данные 2GIS неполные."
    return (
        f"Формула ТЗ: {weighted_spaces:.2f} взвешенных парковочных мест / "
        f"({apartments} квартир × {CAR_OWNERSHIP_COEF}) × 10 = {score:.2f}. "
        f"Фактических мест без весов: {total_spaces}, из них точных 2GIS: {exact_spaces}, "
        f"оценочных после проверки карточек: {estimated_spaces}. "
        f"Домов: {houses}, домов с точным числом квартир: {houses_with_exact_flats}. "
        f"Учитываемых парковочных объектов: {included_parking_objects}, исключено кандидатов: {excluded_parkings}."
    )


def _build_parking_summary_fixed(residential_df: pd.DataFrame, parking_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    zone_defs = [
        ("0–5 минут", 5, ["0–5 минут"]),
        ("5–10 минут", 10, ["5–10 минут"]),
        ("Итого до 10 минут", 10, ["0–5 минут", "5–10 минут"]),
    ]

    for zone_label, minutes, included_zones in zone_defs:
        houses = residential_df[residential_df["Зона"].isin(included_zones)] if residential_df is not None and not residential_df.empty else pd.DataFrame()
        parkings = parking_df[parking_df["Зона"].isin(included_zones)] if parking_df is not None and not parking_df.empty else pd.DataFrame()

        apartments = (
            int(pd.to_numeric(houses.get("Квартир_всего", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
            if not houses.empty
            else 0
        )
        houses_with_data = (
            houses[houses.get("Данные_по_квартирам", pd.Series(dtype=str)).astype(str) == "Да"]
            if not houses.empty
            else pd.DataFrame()
        )
        checked_houses = (
            houses[houses.get("Проверка_карточки_2GIS", pd.Series(dtype=str)).astype(str) == "Да"]
            if not houses.empty
            else pd.DataFrame()
        )

        included_parkings = parkings[parkings["Учитывается_в_расчёте"] == "Да"] if not parkings.empty else pd.DataFrame()
        excluded_parkings = parkings[parkings["Учитывается_в_расчёте"] == "Нет"] if not parkings.empty else pd.DataFrame()

        total_spaces = _supply._sum_spaces(included_parkings)
        free_spaces = _supply._sum_spaces(included_parkings[included_parkings.get("Тип_парковки", pd.Series(dtype=str)).astype(str) == "Бесплатная"]) if not included_parkings.empty else 0
        paid_spaces = _supply._sum_spaces(included_parkings[included_parkings.get("Тип_парковки", pd.Series(dtype=str)).astype(str) == "Платная"]) if not included_parkings.empty else 0
        unknown_spaces = _supply._sum_spaces(included_parkings[included_parkings.get("Тип_парковки", pd.Series(dtype=str)).astype(str) == "Неизвестно"]) if not included_parkings.empty else 0
        estimated_spaces = _supply._sum_spaces(included_parkings[included_parkings.get("Оценочное_значение", pd.Series(dtype=str)).astype(str) == "Да"]) if not included_parkings.empty else 0
        exact_spaces = max(total_spaces - estimated_spaces, 0)
        weighted_spaces = _supply._weighted_spaces_for_zone(included_parkings, zone_label)

        demand = apartments * CAR_OWNERSHIP_COEF
        raw_score = (weighted_spaces / demand * 10.0) if demand > 0 else math.nan
        score = _supply._clip_score(raw_score)
        class_name = _supply._classify_parking_potential(score)
        score_value = round(score, 2) if not math.isnan(score) else pd.NA

        rows.append(
            {
                "Зона": zone_label,
                "Минут_пешком": minutes,
                "Жилых_домов": int(len(houses)),
                "Домов_с_проверенной_карточкой_2GIS": int(len(checked_houses)),
                "Домов_с_данными_по_квартирам": int(len(houses_with_data)),
                "Квартир_в_зоне": apartments,
                "Парковочных_объектов": int(len(included_parkings)),
                "Парковочных_объектов_всего": int(len(parkings)),
                "Парковочных_мест": int(total_spaces),
                "Парковочных_мест_точных_2GIS": int(exact_spaces),
                "Парковочных_мест_оценочных": int(estimated_spaces),
                "Бесплатных_мест": int(free_spaces),
                "Платных_мест": int(paid_spaces),
                "Мест_с_неизвестным_типом": int(unknown_spaces),
                "Исключённых_парковок": int(len(excluded_parkings)),
                "Взвешенных_парковочных_мест": round(weighted_spaces, 2),
                "Парковочный_коэффициент": score_value,
                "Парковочный_потенциал_из_10": score_value,
                "Оценка_из_10": score_value,
                "Класс_обеспеченности": class_name,
                "Класс_парковочного_потенциала": class_name,
                "Комментарий": _build_comment_fixed(
                    apartments,
                    total_spaces,
                    score,
                    len(included_parkings),
                    len(excluded_parkings),
                    weighted_spaces,
                    exact_spaces,
                    estimated_spaces,
                    len(houses),
                    len(houses_with_data),
                ),
                "Коэффициент_владения_авто": CAR_OWNERSHIP_COEF,
                "Расчётная_потребность_машиномест": round(demand, 2) if apartments > 0 else pd.NA,
                "Взвешенный_коэффициент_мест_на_квартиру": round(weighted_spaces / apartments, 3) if apartments > 0 else pd.NA,
                "Мест_по_покупке_аренде": _included_buyable_spaces(parking_df, included_zones),
            }
        )

    columns = list(_supply._summary_columns())
    for extra in (
        "Домов_с_проверенной_карточкой_2GIS",
        "Парковочных_объектов_всего",
        "Парковочных_мест_точных_2GIS",
        "Парковочных_мест_оценочных",
        "Парковочный_потенциал_из_10",
        "Класс_парковочного_потенциала",
        "Мест_по_покупке_аренде",
    ):
        if extra not in columns:
            columns.append(extra)
    return pd.DataFrame(rows).reindex(columns=columns)


def _build_text_outputs_fixed(summary_df: pd.DataFrame) -> tuple[str, str]:
    if summary_df is None or summary_df.empty:
        return "Парковочная обеспеченность не оценена: недостаточно данных 2GIS.", "Парковка: нет данных"

    total_row = summary_df[summary_df["Зона"].astype(str).eq("Итого до 10 минут")] if "Зона" in summary_df.columns else pd.DataFrame()
    if total_row.empty:
        return "Парковочная обеспеченность не оценена: недостаточно данных 2GIS.", "Парковка: нет данных"

    row = total_row.iloc[0]
    score = row.get("Парковочный_потенциал_из_10", row.get("Оценка_из_10"))
    if pd.isna(score):
        houses = int(_supply._safe_float(row.get("Жилых_домов"), 0.0))
        apartments = int(_supply._safe_float(row.get("Квартир_в_зоне"), 0.0))
        return (
            f"Парковочная обеспеченность не оценена: физических жилых домов — {houses}, подтверждённых квартир — {apartments}. "
            "Без подтверждённых квартир формула не применяется.",
            "Парковка: нет данных",
        )

    apartments = int(_supply._safe_float(row.get("Квартир_в_зоне"), 0.0))
    houses = int(_supply._safe_float(row.get("Жилых_домов"), 0.0))
    spaces = int(_supply._safe_float(row.get("Парковочных_мест"), 0.0))
    exact_spaces = int(_supply._safe_float(row.get("Парковочных_мест_точных_2GIS"), 0.0))
    estimated_spaces = int(_supply._safe_float(row.get("Парковочных_мест_оценочных"), 0.0))
    parking_objects = int(_supply._safe_float(row.get("Парковочных_объектов"), 0.0))
    weighted = _supply._safe_float(row.get("Взвешенных_парковочных_мест"), 0.0)
    class_name = row.get("Класс_парковочного_потенциала", row.get("Класс_обеспеченности"))

    return (
        f"Парковочная обеспеченность: в зоне до 10 минут найдено {houses} физических жилых домов, {apartments} квартир "
        f"и {parking_objects} учитываемых парковочных объектов на {spaces} мест "
        f"({exact_spaces} точных 2GIS, {estimated_spaces} оценочных после проверки карточек). "
        f"По формуле: {weighted:.2f} взвешенных мест / ({apartments} квартир × {CAR_OWNERSHIP_COEF}) × 10 = {float(score):.2f}. "
        f"Класс — {class_name}.",
        f"Парковка: {float(score):.2f} / 10, {class_name}",
    )


def _merge_detail_item_into_row(row: dict[str, Any], item: dict[str, Any]) -> None:
    if not isinstance(item, dict):
        return
    row["raw_2gis"] = item

    item_point = item.get("point") or {}
    if item_point.get("lat") is not None:
        row["Широта"] = item_point.get("lat")
    if item_point.get("lon") is not None:
        row["Долгота"] = item_point.get("lon")

    if item.get("attribute_groups") is not None:
        row["attribute_groups"] = item.get("attribute_groups")
    if item.get("description") is not None:
        row["description_2gis"] = item.get("description")
    if item.get("address_name") is not None:
        row["Адрес"] = item.get("address_name")
    if item.get("full_address_name") is not None:
        row["full_address_name"] = item.get("full_address_name")
    if (item.get("geometry") or {}).get("hull") is not None:
        row["geometry_hull"] = (item.get("geometry") or {}).get("hull")

    for target, keys in {
        "access_2gis": ("access",),
        "access_comment_2gis": ("access_comment",),
        "capacity_2gis": ("capacity",),
        "is_paid_2gis": ("is_paid",),
        "for_trucks_2gis": ("for_trucks",),
        "paving_type_2gis": ("paving_type",),
        "is_incentive_2gis": ("is_incentive",),
        "purpose_2gis": ("purpose",),
        "purpose_name_2gis": ("purpose_name",),
        "level_count_2gis": ("level_count",),
        "links_2gis": ("links",),
        "statistics_2gis": ("statistics",),
        "floors_2gis": ("floors", "floor_count", "storeys"),
        "flat_count_2gis": ("flat_count", "flats", "apartments"),
        "entrance_count_2gis": ("entrance_count", "entrances"),
    }.items():
        for key in keys:
            if item.get(key) is not None:
                row[target] = item.get(key)
                break


def _enrich_rows_by_id(rows: list[dict[str, Any]], url: str, api_key: str, timeout: int) -> None:
    index_by_id: dict[str, int] = {}
    for idx, row in enumerate(rows):
        item_id = str(row.get("dgis_id") or "").strip()
        if item_id:
            index_by_id[item_id] = idx

    ids = list(index_by_id.keys())
    if not ids:
        return

    fields = ",".join(
        part.strip()
        for part in f"{PARKING_CAPACITY_FIELDS},{RESIDENTIAL_COUNT_FIELDS}".split(",")
        if part.strip()
    )

    for start in range(0, len(ids), DETAIL_BATCH_SIZE):
        batch = ids[start:start + DETAIL_BATCH_SIZE]
        fetched_items: list[dict[str, Any]] = []
        for attempt in range(3):
            try:
                response = _supply.requests.get(
                    url,
                    params={"id": ",".join(batch), "fields": fields, "key": api_key},
                    timeout=timeout,
                )
                data = response.json()
                fetched_items = _supply._extract_items(data if isinstance(data, dict) else {})
                break
            except Exception as exc:
                try:
                    _supply.logger.warning("2GIS object detail enrich failed attempt=%s: %s", attempt, exc)
                except Exception:
                    pass
                time.sleep(0.5 * (attempt + 1))

        fetched_ids: set[str] = set()
        for item in fetched_items:
            item_id = str(item.get("id") or "").strip()
            row_idx = index_by_id.get(item_id)
            if row_idx is None:
                continue
            fetched_ids.add(item_id)
            row = rows[row_idx]
            _merge_detail_item_into_row(row, item)
            if row.get("_semantic_kind") == "parking":
                row["parking_capacity_checked_2gis"] = True
            if row.get("_semantic_kind") == "residential":
                row["residential_card_checked_2gis"] = True

        for item_id in batch:
            if item_id in fetched_ids:
                continue
            row = rows[index_by_id[item_id]]
            if row.get("_semantic_kind") == "parking":
                row["parking_capacity_checked_2gis"] = False
            if row.get("_semantic_kind") == "residential":
                row["residential_card_checked_2gis"] = False

        if len(ids) > DETAIL_BATCH_SIZE:
            time.sleep(API_PAGE_SLEEP_SEC)


def _offset_points(latitude: float, longitude: float, radius: int) -> list[tuple[float, float]]:
    # Center + 8 points around it. This is still type=building only, but improves
    # coverage in districts where 2GIS returns an incomplete first page around a
    # single POI point.
    if radius <= 0:
        return [(latitude, longitude)]
    lat_delta = (radius * BUILDING_GRID_FACTOR) / 111_320.0
    try:
        lon_delta = (radius * BUILDING_GRID_FACTOR) / (111_320.0 * math.cos(math.radians(latitude)))
    except ZeroDivisionError:
        lon_delta = lat_delta
    offsets = [
        (0.0, 0.0),
        (lat_delta, 0.0),
        (-lat_delta, 0.0),
        (0.0, lon_delta),
        (0.0, -lon_delta),
        (lat_delta, lon_delta),
        (lat_delta, -lon_delta),
        (-lat_delta, lon_delta),
        (-lat_delta, -lon_delta),
    ]
    return [(latitude + dlat, longitude + dlon) for dlat, dlon in offsets]


def _load_type_building_residential_fixed(
    *,
    latitude: float,
    longitude: float,
    radius: int,
    region_id: str,
    settings: Any,
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    seen_ids: set[str] = set()

    for point_lat, point_lon in _offset_points(float(latitude), float(longitude), int(radius)):
        point = f"{float(point_lon)},{float(point_lat)}"
        params = {
            "region_id": region_id,
            "type": "building",
            "point": point,
            "location": point,
            "radius": radius,
            "page_size": _supply.BUILDING_TYPE_PAGE_SIZE,
            "fields": RESIDENTIAL_COUNT_FIELDS,
            "key": settings.dgis_api_key,
            "sort": "distance",
        }
        all_items = _supply._fetch_2gis_items(url, params, _supply.BUILDING_TYPE_MAX_PAGES, settings.dgis_timeout)
        residential_items: list[dict[str, Any]] = []
        for item in all_items:
            item_id = str(item.get("id") or "").strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            if _building_is_residential_fixed(item):
                residential_items.append(item)

        _supply._absorb_2gis_items(
            rows,
            items=residential_items,
            kind="residential",
            rubric_id="type:building",
            rubric_label="Жилой дом",
            region_id=region_id,
            catalog=catalog,
            query_label="type:building",
        )

    try:
        _supply.logger.info("2GIS building loader v26: type=building region_id=%s residential_rows=%d", region_id, len(rows))
    except Exception:
        pass
    return rows


def _load_2gis_api_parking_and_residential(latitude: float | None, longitude: float | None, radius_m: int | None) -> pd.DataFrame:
    """Load parking and residential data only from type=parking/type=building.

    No q-search and no rubric candidates are used for parking potential.
    ``rubric/list`` is used only as a dictionary for official names inside
    _absorb_2gis_items, not as a source of candidate objects.
    """
    if latitude is None or longitude is None:
        return pd.DataFrame()

    settings = _supply.get_settings()
    radius = int(radius_m or settings.poi_radius_m)
    path = _cache_path_api_contract(float(latitude), float(longitude), radius)

    if settings.use_cache and not settings.refresh_cache and path.exists():
        try:
            return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass

    if settings.no_api:
        return pd.DataFrame()

    region = _supply.get_region_for_point(float(latitude), float(longitude))
    if not getattr(region, "id", None):
        try:
            _supply.logger.warning("2GIS parking loader: не удалось определить region_id для точки.")
        except Exception:
            pass
        return pd.DataFrame()

    dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
    locale = str(dgis_config.get("category_catalog_locale") or "ru_RU").strip() or "ru_RU"
    catalog = _supply.load_or_fetch_category_catalog(
        region_id=region.id,
        locale=locale,
        refresh=bool(settings.refresh_cache),
    )

    rows: list[dict[str, Any]] = []
    rows.extend(
        _supply._load_type_parking_objects(
            latitude=float(latitude),
            longitude=float(longitude),
            radius=radius,
            region_id=region.id,
            settings=settings,
            catalog=catalog,
        )
    )
    rows.extend(
        _load_type_building_residential_fixed(
            latitude=float(latitude),
            longitude=float(longitude),
            radius=radius,
            region_id=region.id,
            settings=settings,
            catalog=catalog,
        )
    )

    _enrich_rows_by_id(rows, f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items", settings.dgis_api_key, settings.dgis_timeout)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = _deduplicate_fixed(df)

    if settings.use_cache:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(df.to_dict("records"), ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            pass

    return df


def _apply_runtime_patch() -> None:
    _supply.PARKING_LOADER_VERSION = PARKING_LOADER_VERSION
    _supply.PARKING_QUERIES = []
    _supply.RESIDENTIAL_QUERIES = []
    _supply._cache_path = _cache_path_api_contract
    _supply._load_semantic_parking_and_residential = _load_2gis_api_parking_and_residential
    _supply._building_is_residential = _building_is_residential_fixed
    _supply._load_type_building_residential = _load_type_building_residential_fixed
    _supply._is_residential_building = _is_residential_building_fixed
    _supply._is_parking_object = _is_parking_object_fixed
    _supply._classify_parking_type = _classify_parking_type_fixed
    _supply._deduplicate = _deduplicate_fixed
    _supply._is_flat_count_label = _is_flat_count_label_fixed
    _supply._extract_apartments = _extract_apartments_fixed
    _supply._estimate_apartments = _estimate_apartments_fixed
    _supply._extract_parking_spaces = _extract_parking_spaces_fixed
    _supply._build_parking_details = _build_parking_details_fixed
    _supply._build_residential_details = _build_residential_details_fixed
    _supply._build_parking_summary = _build_parking_summary_fixed
    _supply._build_text_outputs = _build_text_outputs_fixed
    _supply._parking_detail_columns = _parking_detail_columns_fixed


_apply_runtime_patch()


def calculate_parking_supply(*args: Any, **kwargs: Any) -> ParkingSupplyResult:
    _apply_runtime_patch()
    return _supply.calculate_parking_supply(*args, **kwargs)


def calculate_parking_potential(weighted_parking_spaces: float, apartments: float) -> float | None:
    if apartments is None or apartments <= 0:
        return None
    score = float(weighted_parking_spaces or 0) / (float(apartments) * CAR_OWNERSHIP_COEF) * 10
    return round(max(0.0, min(10.0, score)), 2)


def classify_parking_potential(score: float | None) -> str:
    if score is None:
        return "Нет данных"
    try:
        if math.isnan(float(score)):
            return "Нет данных"
    except (TypeError, ValueError):
        return "Нет данных"
    if float(score) >= 8:
        return "Высокий"
    if float(score) >= 4:
        return "Средний"
    return "Низкий"


__all__ = [
    "BUYABLE_SPACE_MARKERS",
    "CAR_OWNERSHIP_COEF",
    "CLOSED_PARKING_MARKERS",
    "DEFAULT_APARTMENTS_PER_BUILDING",
    "PARKING_LOADER_VERSION",
    "ParkingSupplyResult",
    "calculate_parking_potential",
    "calculate_parking_supply",
    "classify_parking_potential",
]
