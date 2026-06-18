"""Юнит-тесты автономного конкурентного анализа.

Сетевые вызовы 2GIS не выполняются: данные передаются через ``pois``, а
координаты задаются явно, поэтому собственный загрузчик 2GIS не дёргается
(офлайн-режим через ``GEO_ANALYZER_NO_API``).
"""

from __future__ import annotations

import pandas as pd
import pytest

from geo_analyzer.competition import analyze_competition
from geo_analyzer.core.settings import clear_settings_cache


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # Исключаем сетевой добор из 2GIS и кеша — считаем только переданные pois.
    monkeypatch.setenv("GEO_ANALYZER_NO_API", "1")
    monkeypatch.setenv("GEO_ANALYZER_DISABLE_CACHE", "1")
    clear_settings_cache()
    yield
    clear_settings_cache()


# Центр анализа.
_LAT, _LON = 56.8526, 53.2115


def test_filters_out_non_residential():
    pois = pd.DataFrame(
        [
            {"Название": "ЖК Радуга", "Широта": 56.853, "Долгота": 53.212},
            {"Название": "Магазин продуктов", "Широта": 56.853, "Долгота": 53.212},
        ]
    )
    result = analyze_competition(pois=pois, latitude=_LAT, longitude=_LON)
    names = set(result.competitors["Название"])
    assert "ЖК Радуга" in names
    assert "Магазин продуктов" not in names


def test_counts_new_and_under_construction():
    pois = pd.DataFrame(
        [
            {"Название": "ЖК Новый, строящийся дом", "Широта": 56.854, "Долгота": 53.213},
            {"Название": "ЖК Старый жилой комплекс", "Широта": 56.855, "Долгота": 53.214},
        ]
    )
    result = analyze_competition(pois=pois, latitude=_LAT, longitude=_LON)
    total = result.summary[result.summary["Зона_расстояния"] == "Итого"].iloc[0]
    assert int(total["Конкурентов_всего"]) == 2
    assert int(total["Новых_или_строящихся"]) == 1


def test_units_from_attributes_and_distance_band():
    attribute_groups = [
        {"attributes": [{"name": "Количество квартир", "value": "320"}]}
    ]
    pois = pd.DataFrame(
        [
            {
                "Название": "ЖК Премьер",
                "Широта": 56.8530,
                "Долгота": 53.2120,
                "attribute_groups": attribute_groups,
            }
        ]
    )
    result = analyze_competition(pois=pois, latitude=_LAT, longitude=_LON)
    row = result.competitors.iloc[0]
    assert int(row["Квартир_оценка"]) == 320
    assert row["Оценка_квартир"] == "Нет"
    assert row["Зона_расстояния"] == "0–500 м"


def test_developers_summary():
    pois = pd.DataFrame(
        [
            {"Название": "ЖК А", "Широта": 56.853, "Долгота": 53.212, "attribute_groups": [{"attributes": [{"name": "Застройщик", "value": "СтройИнвест"}]}]},
            {"Название": "ЖК Б", "Широта": 56.854, "Долгота": 53.213, "attribute_groups": [{"attributes": [{"name": "Застройщик", "value": "СтройИнвест"}]}]},
        ]
    )
    result = analyze_competition(pois=pois, latitude=_LAT, longitude=_LON)
    assert not result.developers.empty
    top = result.developers.iloc[0]
    assert top["Застройщик"] == "СтройИнвест"
    assert int(top["Объектов"]) == 2


def test_empty_when_no_complexes():
    pois = pd.DataFrame([{"Название": "Автосервис", "Широта": 56.853, "Долгота": 53.212}])
    result = analyze_competition(pois=pois, latitude=_LAT, longitude=_LON)
    assert result.competitors.empty
    assert "нет" in result.gui_label.lower() or result.summary.empty


def test_institutional_complex_is_not_a_competitor():
    # «учебно-жилой комплекс» колледжа — не конкурент застройщику.
    pois = pd.DataFrame(
        [
            {"Название": "Медицинский колледж, учебно-жилой комплекс", "Широта": 56.853, "Долгота": 53.212},
            {"Название": "ЖК Виктория", "Широта": 56.853, "Долгота": 53.212},
        ]
    )
    result = analyze_competition(pois=pois, latitude=_LAT, longitude=_LON)
    names = set(result.competitors["Название"])
    assert "ЖК Виктория" in names
    assert all("колледж" not in n.lower() for n in names)
