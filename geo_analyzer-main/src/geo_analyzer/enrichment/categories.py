from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import pandas as pd

from geo_analyzer.core.logger import get_logger
from geo_analyzer.ingestion.dgis.category_catalog import normalize_category_name

logger = get_logger("geo_analyzer.enrichment.categories")


@dataclass(frozen=True)
class RuleResult:
    category_2gis: str
    functional_category: str
    criticality_score: int
    classification_rule_id: str
    classification_status: str


DEFAULT_CATEGORY_2GIS = "Прочее"

FUNCTION_EDUCATION = "Образование и развитие"
FUNCTION_MEDICINE = "Медицина и здоровье"
FUNCTION_DAILY = "Повседневная торговля и услуги"
FUNCTION_SPORT = "Спорт и активный отдых"
FUNCTION_LEISURE = "Досуг и городское притяжение"
FUNCTION_SECONDARY = "Вторичные и фоновые услуги"
FUNCTION_TRANSPORT = "Транспорт"
FUNCTION_OTHER = "Прочее"

STOP_RUBRIC_IDS = {"450", "type:station", "type:station_platform"}

SHOPPING_MALL_NAMES = {
    "торговые центры",
    "торговый центр",
    "торгово-развлекательные центры",
    "торгово-развлекательный центр",
    "торгово-развлекательные комплексы",
    "торгово-развлекательный комплекс",
    "торговые комплексы",
    "торговый комплекс",
}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def _as_list(value: Any) -> list[str]:
    if _is_empty(value):
        return []
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_as_list(item))
        return values
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_as_list(item))
        return result
    text = normalize_category_name(value)
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _text(row: pd.Series, columns: list[str]) -> str:
    values: list[str] = []
    for column in columns:
        if column not in row.index:
            continue
        values.extend(_as_list(row.get(column)))
    return " ".join(values)


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _category_text(row: pd.Series) -> str:
    return _text(
        row,
        [
            "Категория_2GIS_официальная",
            "Категория_2GIS",
            "source_category_2gis",
            "source_categories_2gis",
            "rubrics_2gis",
            "category_groups_2gis",
            "rubric_id",
            "object_type_2gis",
            "object_subtype_2gis",
        ],
    )


def _combined_text(row: pd.Series) -> str:
    return _text(
        row,
        [
            "Название",
            "name",
            "Адрес",
            "address",
            "Категория_2GIS_официальная",
            "Категория_2GIS",
            "Категория",
            "category",
            "source_category_2gis",
            "source_categories_2gis",
            "rubrics_2gis",
            "category_groups_2gis",
            "rubric_id",
            "object_type_2gis",
            "object_subtype_2gis",
            "routes_2gis",
            "platforms_2gis",
        ],
    )


def _best_category_label(row: pd.Series, fallback: str = DEFAULT_CATEGORY_2GIS) -> str:
    object_type = normalize_category_name(row.get("object_type_2gis") if "object_type_2gis" in row.index else "")
    if object_type in {"station", "station_platform"}:
        return "Остановка общественного транспорта"
    for column in [
        "Категория_2GIS_официальная",
        "Категория_2GIS",
        "source_category_2gis",
        "rubrics_2gis",
        "source_categories_2gis",
        "Категория",
        "category",
    ]:
        if column not in row.index:
            continue
        values = _as_list(row.get(column))
        if values:
            return values[0].capitalize()
    return fallback


def _is_transport_stop(row: pd.Series) -> bool:
    object_type = normalize_category_name(row.get("object_type_2gis") if "object_type_2gis" in row.index else "")
    if object_type in {"station", "station_platform"}:
        return True
    text = _category_text(row)
    if _contains_any(
        text,
        {
            "остановка общественного транспорта",
            "остановки общественного транспорта",
            "остановочный пункт",
            "станция общественного транспорта",
            "public transport stop",
            "station",
            "station_platform",
        },
    ):
        return True

    rubric_id = normalize_category_name(row.get("rubric_id")) if "rubric_id" in row.index else ""
    if rubric_id in STOP_RUBRIC_IDS:
        return True

    for value in _as_list(row.get("category_groups_2gis") if "category_groups_2gis" in row.index else None):
        if value in STOP_RUBRIC_IDS:
            return True

    return False


def _is_shopping_mall(row: pd.Series) -> bool:
    text = _category_text(row)
    if _contains_any(text, SHOPPING_MALL_NAMES):
        return True
    combined = _combined_text(row)
    return re.search(r"(?<![а-яa-z0-9])(тц|трц|трк)(?![а-яa-z0-9])", combined) is not None


def _is_vending_or_game_machine(row: pd.Series) -> bool:
    text = _combined_text(row)
    return _contains_any(
        text,
        {
            "игровой автомат",
            "игровые автоматы",
            "вендинг",
            "вендинговый автомат",
            "торговый автомат",
        },
    )


def _is_online_or_warehouse(row: pd.Series) -> bool:
    text = _combined_text(row)
    return _contains_any(
        text,
        {
            "интернет-магазин",
            "интернет магазин",
            "online",
            "онлайн-магазин",
            "онлайн магазин",
            "склад",
            "оптовый склад",
            "офис продаж",
            "офис компании",
            "офис",
            "автозапчаст",
            "запчасти",
            "автотовары",
            "шины",
            "диски",
        },
    )


def _is_city_scale_attraction(row: pd.Series) -> bool:
    if _is_shopping_mall(row):
        return True

    text = _category_text(row)
    allowed = {
        "парки",
        "скверы",
        "театры",
        "музеи",
        "стадионы / спортивные арены",
        "стадионы",
        "спортивные арены",
        "выставочные центры",
        "зоопарки",
        "аквапарки",
        "парки развлечений",
        "кинотеатры",
        "филармонии",
        "цирки",
    }
    return _contains_any(text, allowed)


def _classify_by_rules(row: pd.Series) -> RuleResult:
    # Обратная совместимость со старыми OSM/legacy-данными и юнит-тестами:
    # если объект пришёл без официальной категории 2GIS, но содержит shop=supermarket,
    # классифицируем его как супермаркет.
    legacy_shop = normalize_category_name(row.get("shop") if "shop" in row.index else "")
    if legacy_shop == "supermarket":
        return RuleResult("Супермаркет", FUNCTION_DAILY, 8, "legacy_osm_shop_supermarket", "mapped")

    category_text = _category_text(row)
    category_label = _best_category_label(row)

    if _is_transport_stop(row):
        return RuleResult("Остановка общественного транспорта", FUNCTION_TRANSPORT, 8, "transport_stop_2gis_type_or_category", "mapped")

    if _is_vending_or_game_machine(row):
        return RuleResult(category_label, FUNCTION_OTHER, 0, "force_vending_to_other", "forced_to_other")

    if _is_shopping_mall(row):
        return RuleResult(category_label, FUNCTION_LEISURE, 8, "shopping_mall_official_category", "mapped")

    if _is_online_or_warehouse(row):
        return RuleResult(category_label, FUNCTION_SECONDARY, 0, "online_warehouse_secondary", "mapped")

    if _contains_any(category_text, {"школы", "школа", "лицеи", "гимназии", "детские сады", "детский сад"}):
        return RuleResult(category_label, FUNCTION_EDUCATION, 8, "education_school_kindergarten_official_category", "mapped")

    if _contains_any(
        category_text,
        {
            "центры раннего развития детей",
            "детские игровые залы",
            "детское дополнительное образование",
            "развитие детей",
            "профессиональная переподготовка",
            "переподготовка и повышение квалификации",
            "повышение квалификации",
            "взрослое дополнительное образование",
        },
    ):
        return RuleResult(category_label, FUNCTION_EDUCATION, 5, "education_extra_official_category", "mapped")

    if _contains_any(category_text, {"аптеки", "аптека"}):
        return RuleResult(category_label, FUNCTION_MEDICINE, 8, "pharmacy_official_category", "mapped")

    if _contains_any(category_text, {"поликлиники", "медицинские центры", "мед.центры", "клиники", "больницы", "многопрофильные медицинские центры"}):
        return RuleResult(category_label, FUNCTION_MEDICINE, 5, "medicine_official_category", "mapped")

    if _contains_any(category_text, {"ветеринарные клиники", "зоотовары"}):
        return RuleResult(category_label, FUNCTION_MEDICINE, 3, "pet_services_official_category", "mapped")

    if _contains_any(category_text, {"супермаркеты", "продуктовые магазины", "гипермаркеты", "магазины продуктов"}):
        return RuleResult(category_label, FUNCTION_DAILY, 8, "grocery_official_category", "mapped")

    if _contains_any(category_text, {"пункты выдачи интернет-заказов", "пункты выдачи заказов"}):
        return RuleResult(category_label, FUNCTION_DAILY, 10, "pickup_points_official_category", "mapped")

    if _contains_any(category_text, {"кофейни", "пекарни", "кондитерские"}):
        return RuleResult(category_label, FUNCTION_DAILY, 4, "coffee_bakery_official_category", "mapped")

    if _contains_any(category_text, {"парикмахерские", "ногтевые студии", "салоны красоты", "маникюр и педикюр"}):
        return RuleResult(category_label, FUNCTION_DAILY, 4, "beauty_official_category", "mapped")

    if _contains_any(category_text, {"фитнес-клубы", "тренажёрные залы", "тренажерные залы", "спортивные комплексы"}):
        return RuleResult(category_label, FUNCTION_SPORT, 3, "sport_official_category", "mapped")

    if _contains_any(category_text, {"кафе", "рестораны", "бары"}):
        return RuleResult(category_label, FUNCTION_LEISURE, 3, "food_full_cycle_official_category", "mapped")

    if _is_city_scale_attraction(row):
        return RuleResult(category_label, FUNCTION_LEISURE, 8, "city_scale_attraction_official_category", "mapped")

    if _contains_any(category_text, {"химчистки", "ремонт обуви", "швейные ателье", "ремонт и установка бытовой техники", "установка", "хозтовары"}):
        return RuleResult(category_label, FUNCTION_SECONDARY, 0, "secondary_services_official_category", "mapped")

    return RuleResult(
        category_2gis=category_label,
        functional_category=FUNCTION_OTHER,
        criticality_score=0,
        classification_rule_id="official_category_not_mapped_to_function",
        classification_status="not_mapped",
    )


def classify_pois(pois_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    output_columns = [
        "Категория_2GIS",
        "functional_category",
        "criticality_score",
        "classification_rule_id",
        "classification_status",
        "validation_status",
        "category",
        "Категория",
        "Сценарная_группа",
    ]

    if pois_gdf is None or pois_gdf.empty:
        columns = list(getattr(pois_gdf, "columns", []))
        for column in output_columns:
            if column not in columns:
                columns.append(column)
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs="EPSG:4326")

    gdf = pois_gdf.copy()
    results = gdf.apply(_classify_by_rules, axis=1)

    gdf["Категория_2GIS"] = [item.category_2gis for item in results]
    gdf["functional_category"] = [item.functional_category for item in results]
    gdf["criticality_score"] = [item.criticality_score for item in results]
    gdf["classification_rule_id"] = [item.classification_rule_id for item in results]
    gdf["classification_status"] = [item.classification_status for item in results]

    source_validation = (
        gdf["category_validation_status"].astype(str)
        if "category_validation_status" in gdf.columns
        else pd.Series(["unknown"] * len(gdf), index=gdf.index)
    )
    gdf["validation_status"] = source_validation.where(
        source_validation.eq("official_catalog_match"),
        gdf["classification_status"].apply(lambda value: "ok" if value in {"mapped", "forced_to_other"} else "needs_review"),
    )

    gdf["category"] = gdf["Категория_2GIS"]
    gdf["Категория"] = gdf["Категория_2GIS"]
    gdf["Сценарная_группа"] = gdf["functional_category"]

    logger.info(
        "POI классифицированы по официальным категориям 2GIS: %s объектов, %s категорий",
        len(gdf),
        gdf["Категория_2GIS"].nunique(dropna=True),
    )

    return gdf
