from __future__ import annotations

import pandas as pd
from shapely.geometry import Point, box

from geo_analyzer.parking import CAR_OWNERSHIP_COEF, DEFAULT_APARTMENTS_PER_BUILDING, calculate_parking_supply
from geo_analyzer.parking.supply import PAID_PARKING_WEIGHT, UNKNOWN_PARKING_WEIGHT, _building_is_residential
from geo_analyzer.metrics.parking_supply import calculate_parking_supply as _legacy_calculate_parking_supply  # noqa: F401


def _isochrones() -> pd.DataFrame:
    ring = box(52.9, 56.6, 53.5, 57.1)
    return pd.DataFrame([{"minutes": 5, "geometry": ring}, {"minutes": 10, "geometry": ring}])


def _point() -> Point:
    return Point(53.21, 56.85)


def _house(**extra) -> dict:
    row = {
        "Название": "улица Карла Маркса, 263",
        "Адрес": "улица Карла Маркса, 263",
        "Категория_2GIS": "Жилой дом",
        "_semantic_kind": "residential",
        "Поисковый_запрос": "type:building",
        "rubric_id": "type:building",
        "residential_card_checked_2gis": True,
        "geometry": _point(),
    }
    row.update(extra)
    return row


def _parking(name: str = "Городская парковка", **extra) -> dict:
    row = {
        "Название": name,
        "Категория_2GIS": "Парковка",
        "_semantic_kind": "parking",
        "Поисковый_запрос": "type:parking",
        "rubric_id": "type:parking",
        "parking_capacity_checked_2gis": True,
        "geometry": _point(),
    }
    row.update(extra)
    return row


def test_plain_text_objects_are_not_candidates():
    pois = pd.DataFrame([{"Название": "Жилой дом", "geometry": _point()}, {"Название": "Городская парковка на 40 мест", "geometry": _point()}])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    total = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]
    assert result.residential_details.empty
    assert result.parking_details.empty
    assert int(total["Жилых_домов"]) == 0
    assert int(total["Парковочных_объектов"]) == 0


def test_capacity_is_read_from_2gis_attributes_when_type_parking():
    attribute_groups = [{"attributes": [{"tag": "capacity", "name": "Машино-мест", "value": "120"}]}]
    parking = calculate_parking_supply(pois=pd.DataFrame([_parking(attribute_groups=attribute_groups, access_2gis="Общедоступная")]), isochrones=_isochrones()).parking_details
    assert int(parking.iloc[0]["Парковочных_мест"]) == 120
    assert parking.iloc[0]["Данные_по_местам"] == "Да"


def test_apartments_are_read_from_2gis_attributes_when_type_building():
    attribute_groups = [{"attributes": [{"tag": "flat_count", "name": "Количество квартир", "value": "240"}]}]
    residential = calculate_parking_supply(pois=pd.DataFrame([_house(attribute_groups=attribute_groups)]), isochrones=_isochrones()).residential_details
    assert int(residential.iloc[0]["Квартир_всего"]) == 240
    assert residential.iloc[0]["Данные_по_квартирам"] == "Да"


def test_apartments_are_read_from_2gis_structure_info_when_type_building():
    raw = {"structure_info": {"apartments_count": 318, "porch_count": 5, "floors": 16}}
    residential = calculate_parking_supply(
        pois=pd.DataFrame([_house(raw_2gis=raw, flat_count_2gis=318, entrance_count_2gis=5, floors_2gis=16)]),
        isochrones=_isochrones(),
    ).residential_details
    assert int(residential.iloc[0]["Квартир_всего"]) == 318
    assert residential.iloc[0]["Данные_по_квартирам"] == "Да"


def test_apartment_estimate_requires_checked_2gis_card():
    residential = calculate_parking_supply(pois=pd.DataFrame([_house(residential_card_checked_2gis=False)]), isochrones=_isochrones()).residential_details
    assert int(residential.iloc[0]["Квартир_всего"]) == 0
    assert "оценка запрещена" in residential.iloc[0]["Метод_расчёта"]


def test_potential_formula_uses_type_parking_only():
    pois = pd.DataFrame([
        _house(),
        _parking("Paid parking", capacity_2gis=100, is_paid_2gis=True),
        _parking("Public parking", capacity_2gis=40, access_2gis="Общедоступная"),
    ])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    total = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]
    assert int(total["Квартир_в_зоне"]) == DEFAULT_APARTMENTS_PER_BUILDING
    assert int(total["Парковочных_мест"]) == 140
    demand = DEFAULT_APARTMENTS_PER_BUILDING * CAR_OWNERSHIP_COEF
    expected = round((100 * PAID_PARKING_WEIGHT + 40 * UNKNOWN_PARKING_WEIGHT) / demand * 10, 2)
    assert float(total["Оценка_из_10"]) == expected
    assert float(total["Парковочный_потенциал_из_10"]) == expected


def test_dropoff_is_not_parking_object():
    result = calculate_parking_supply(pois=pd.DataFrame([_parking("место высадки 1", capacity_2gis=3, access_2gis="Общедоступная")]), isochrones=_isochrones())
    assert result.parking_details.empty


def test_building_is_residential_default_include():
    assert _building_is_residential({"name": "улица Карла Маркса, 263"}) is True
    assert _building_is_residential({"name": "Commercial center"}) is True
    assert _building_is_residential({"purpose": "Многоквартирный жилой дом"}) is True
