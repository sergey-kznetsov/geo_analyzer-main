from __future__ import annotations

import pandas as pd

import geo_analyzer.metrics.anti_driver_score as anti
from geo_analyzer.metrics.anti_driver_score import detect_anti_drivers


def test_anti_driver_module_has_no_semantic_loader():
    assert not hasattr(anti, "_semantic_rows")
    assert not hasattr(anti, "SEMANTIC_QUERIES")


def test_anti_driver_detection_uses_loaded_poi_only():
    pois = pd.DataFrame(
        [
            {
                "Название": "Городская АЗС",
                "Адрес": "улица Ленина, 1",
                "Категория_2GIS": "АЗС",
                "Минут_пешком": 5,
                "Зона_доступности": "0–5 минут",
            }
        ]
    )

    result = detect_anti_drivers(pois, latitude=56.85, longitude=53.21, radius_m=1200)

    assert len(result) == 1
    assert result.iloc[0]["Тип_антидрайвера"] == "АЗС"


def test_anti_driver_detection_does_not_use_address_text():
    pois = pd.DataFrame(
        [
            {
                "Название": "Кофейня у дома",
                "Адрес": "Железнодорожная улица, 10",
                "Категория_2GIS": "Кофейни",
                "Минут_пешком": 5,
                "Зона_доступности": "0–5 минут",
            }
        ]
    )

    result = detect_anti_drivers(pois)

    assert result.empty
