from __future__ import annotations

import pandas as pd
from shapely.geometry import Point, box

from geo_analyzer.parking import calculate_parking_supply


def _isochrones() -> pd.DataFrame:
    ring = box(52.9, 56.6, 53.5, 57.1)
    return pd.DataFrame([{"minutes": 5, "geometry": ring}, {"minutes": 10, "geometry": ring}])


def _point() -> Point:
    return Point(53.21, 56.85)


def _type_parking(name: str, **extra) -> dict:
    row = {
        "Название": name,
        "Категория_2GIS": "Паркинги",
        "_semantic_kind": "parking",
        "Поисковый_запрос": "type:parking",
        "rubric_id": "type:parking",
        "parking_capacity_checked_2gis": True,
        "geometry": _point(),
    }
    row.update(extra)
    return row


def test_regular_poi_with_capacity_is_not_parking():
    pois = pd.DataFrame([{"Название": "Урал Пром-Комплект", "Категория_2GIS": "Строительство", "capacity_2gis": 1, "is_paid_2gis": True, "geometry": _point()}])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    assert result.parking_details.empty


def test_type_parking_is_counted_when_access_is_not_restricted():
    pois = pd.DataFrame([_type_parking("Парковка", capacity_2gis=70, is_paid_2gis=True, access_2gis="Общедоступная")])
    parking = calculate_parking_supply(pois=pois, isochrones=_isochrones()).parking_details
    row = parking.iloc[0]
    assert row["Учитывается_в_расчёте"] == "Да"
    assert row["Тип_парковки"] == "Платная"
    assert int(row["Парковочных_мест"]) == 70


def test_residential_project_card_is_not_counted_as_physical_house():
    pois = pd.DataFrame([{"Название": "ДОММ, строящийся жилой комплекс", "Адрес": "улица Максима Горького, 153", "Категория_2GIS": "Новостройки", "_semantic_kind": "residential", "Поисковый_запрос": "type:building", "rubric_id": "type:building", "geometry": _point()}])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    assert result.residential_details.empty


def test_type_building_address_is_counted_as_physical_house():
    pois = pd.DataFrame([{"Название": "улица Карла Маркса, 263", "Адрес": "улица Карла Маркса, 263", "Категория_2GIS": "Жилой дом", "_semantic_kind": "residential", "Поисковый_запрос": "type:building", "rubric_id": "type:building", "residential_card_checked_2gis": True, "geometry": _point()}])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    assert len(result.residential_details) == 1
    total = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]
    assert int(total["Жилых_домов"]) == 1
    assert int(total["Квартир_в_зоне"]) > 0
