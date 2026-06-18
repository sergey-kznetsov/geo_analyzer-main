from __future__ import annotations

import pandas as pd
from shapely.geometry import Point, box

from geo_analyzer.metrics.parking_supply import ParkingSupplyResult, calculate_parking_supply
from geo_analyzer.parking import DEFAULT_APARTMENTS_PER_BUILDING
from geo_analyzer.parking.fixes import CAR_OWNERSHIP_COEF


def _isochrones() -> pd.DataFrame:
    ring_5 = box(52.9, 56.6, 53.5, 57.1)
    ring_10 = box(52.8, 56.5, 53.6, 57.2)
    return pd.DataFrame([
        {"minutes": 5, "geometry": ring_5},
        {"minutes": 10, "geometry": ring_10},
    ])


def _point() -> Point:
    return Point(53.21, 56.85)


def test_metrics_wrapper_exports_result_class():
    assert ParkingSupplyResult is not None


def test_formula_aliases_are_present_in_summary_for_type_rows():
    pois = pd.DataFrame([
        {
            "Название": "улица Карла Маркса, 263",
            "geometry": _point(),
            "_semantic_kind": "residential",
            "Поисковый_запрос": "type:building",
            "rubric_id": "type:building",
            "residential_card_checked_2gis": True,
        },
        {
            "Название": "Городская парковка",
            "geometry": _point(),
            "_semantic_kind": "parking",
            "Поисковый_запрос": "type:parking",
            "rubric_id": "type:parking",
            "capacity_2gis": 40,
            "access_2gis": "Общедоступная",
            "parking_capacity_checked_2gis": True,
        },
    ])

    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    row = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]

    assert "Парковочный_потенциал_из_10" in result.summary.columns
    assert "Класс_парковочного_потенциала" in result.summary.columns
    assert int(row["Жилых_домов"]) == 1
    assert int(row["Квартир_в_зоне"]) == DEFAULT_APARTMENTS_PER_BUILDING

    expected = round(40 * 0.75 / (DEFAULT_APARTMENTS_PER_BUILDING * CAR_OWNERSHIP_COEF) * 10, 2)
    assert float(row["Оценка_из_10"]) == expected
    assert float(row["Парковочный_потенциал_из_10"]) == expected


def test_plain_text_poi_is_not_counted_as_house_or_parking():
    pois = pd.DataFrame([
        {"Название": "Жилой дом", "geometry": _point()},
        {"Название": "Городская парковка на 40 мест", "geometry": _point()},
    ])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    row = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]
    assert int(row["Жилых_домов"]) == 0
    assert int(row["Парковочных_объектов"]) == 0
