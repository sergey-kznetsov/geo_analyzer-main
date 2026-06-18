from __future__ import annotations

import pandas as pd
from shapely.geometry import Point, box

from geo_analyzer.parking import calculate_parking_supply
from geo_analyzer.parking.contract_v27 import _absorb, _parse_geometry


def _isochrones() -> pd.DataFrame:
    ring = box(52.9, 56.6, 53.5, 57.1)
    return pd.DataFrame([
        {"minutes": 5, "geometry": ring},
        {"minutes": 10, "geometry": ring},
    ])


def _point() -> Point:
    return Point(53.21, 56.85)


def _house(**extra) -> dict:
    row = {
        "Название": "улица Карла Маркса, 263",
        "Адрес": "улица Карла Маркса, 263",
        "_semantic_kind": "residential",
        "Поисковый_запрос": "type:building",
        "rubric_id": "type:building",
        "residential_card_checked_2gis": True,
        "flat_count_2gis": 100,
        "geometry": _point(),
    }
    row.update(extra)
    return row


def _parking(**extra) -> dict:
    row = {
        "Название": "Парковка",
        "_semantic_kind": "parking",
        "Поисковый_запрос": "type:parking",
        "rubric_id": "type:parking",
        "capacity_2gis": 40,
        "geometry": _point(),
    }
    row.update(extra)
    return row


def test_type_parking_with_capacity_is_counted_when_not_restricted():
    pois = pd.DataFrame([_house(), _parking(is_paid_2gis=False)])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    total = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]
    assert int(total["Парковочных_мест"]) == 40
    assert int(total["Бесплатных_мест"]) == 40
    assert result.parking_details.iloc[0]["Учитывается_в_расчёте"] == "Да"


def test_resident_only_parking_is_excluded_even_with_capacity():
    pois = pd.DataFrame([_house(), _parking(access_2gis="Только для резидентов", is_paid_2gis=False)])
    result = calculate_parking_supply(pois=pois, isochrones=_isochrones())
    total = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]
    assert int(total["Парковочных_мест"]) == 0
    assert result.parking_details.iloc[0]["Учитывается_в_расчёте"] == "Нет"


def test_building_geometry_centroid_is_preserved_by_absorb():
    rows: list[dict] = []
    _absorb(
        rows,
        items=[
            {
                "id": "b1",
                "name": "улица Карла Маркса, 263",
                "address_name": "улица Карла Маркса, 263",
                "geometry": {"centroid": {"lat": 56.85, "lon": 53.21}},
                "flat_count": 120,
            }
        ],
        kind="residential",
        rubric_id="type:building",
        rubric_label="Жилой дом",
        region_id="1",
        catalog={},
        query_label="type:building",
    )
    assert rows[0]["Широта"] == 56.85
    assert rows[0]["Долгота"] == 53.21


def test_geometry_parser_accepts_2gis_centroid_dict():
    geom = _parse_geometry({"centroid": {"lat": 56.85, "lon": 53.21}})
    assert isinstance(geom, Point)
    assert round(geom.x, 2) == 53.21
    assert round(geom.y, 2) == 56.85
