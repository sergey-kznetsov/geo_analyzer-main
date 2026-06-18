from __future__ import annotations

import pandas as pd
from shapely.geometry import Point, box

from geo_analyzer.parking import calculate_parking_supply


def test_v23_compatibility_smoke():
    ring = box(52.9, 56.6, 53.5, 57.1)
    point = Point(53.21, 56.85)
    pois = pd.DataFrame([
        {"Название": "house", "_semantic_kind": "residential", "Поисковый_запрос": "type:building", "rubric_id": "type:building", "residential_card_checked_2gis": True, "flat_count_2gis": 100, "geometry": point},
        {"Название": "parking", "_semantic_kind": "parking", "Поисковый_запрос": "type:parking", "rubric_id": "type:parking", "capacity_2gis": 20, "geometry": point},
    ])
    result = calculate_parking_supply(pois=pois, isochrones=pd.DataFrame([{"minutes": 5, "geometry": ring}, {"minutes": 10, "geometry": ring}]))
    total = result.summary[result.summary["Зона"] == "Итого до 10 минут"].iloc[0]
    assert int(total["Жилых_домов"]) == 1
    assert int(total["Квартир_в_зоне"]) == 100
    assert int(total["Парковочных_мест"]) == 20
