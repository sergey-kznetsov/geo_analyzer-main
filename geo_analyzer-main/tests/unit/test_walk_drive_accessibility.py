import pandas as pd

from geo_analyzer.metrics.accessibility import build_accessibility_snapshot


def test_walk_and_drive_accessibility_are_separated():
    poi_details = pd.DataFrame(
        [
            {
                "Минут_пешком": 5,
                "Категория_2GIS": "Супермаркет",
                "functional_category": "Повседневная торговля и услуги",
                "Название": "Магнит",
                "dgis_id": "shop-1",
            },
            {
                "Минут_пешком": 5,
                "Категория_2GIS": "Остановка общественного транспорта",
                "functional_category": "Транспорт",
                "Название": "Остановка А",
                "dgis_id": "stop-1",
            },
            {
                "Минут_пешком": 5,
                "Категория_2GIS": "Остановка общественного транспорта",
                "functional_category": "Транспорт",
                "Название": "Остановка Б",
                "dgis_id": "stop-2",
            },
        ]
    )

    drive_metrics = {
        "drive_time_min": 8,
        "drive_distance_km": 3.2,
        "walk_time_min": 42,
        "walk_distance_km": 3.1,
        "center_name": "Центральная площадь",
        "center_city": "Ижевск",
        "data_source": "2gis_routing_api",
    }

    result = build_accessibility_snapshot(
        poi_counts_by_iso=None,
        poi_details_by_iso=poi_details,
        drive_metrics=drive_metrics,
    )

    first_zone = result[result["Минут_пешком"].eq(5)].iloc[0]

    assert first_zone["Пешая_доступность_из_10"] > 0
    assert first_zone["Остановочная_доступность_из_10"] == 5.0
    assert first_zone["Авто_доступность_до_центра_из_10"] == 8.5
    assert first_zone["Источник_авто_метрики"] == "2gis_routing_api"


def test_accessibility_does_not_count_drive_score_as_stop_score():
    poi_details = pd.DataFrame(
        [
            {
                "Минут_пешком": 5,
                "Категория_2GIS": "Супермаркет",
                "functional_category": "Повседневная торговля и услуги",
                "Название": "Магнит",
                "dgis_id": "shop-1",
            }
        ]
    )

    drive_metrics = {
        "drive_time_min": 5,
        "drive_distance_km": 2.0,
        "center_name": "Центр",
        "center_city": "Ижевск",
        "data_source": "2gis_routing_api",
    }

    result = build_accessibility_snapshot(
        poi_counts_by_iso=None,
        poi_details_by_iso=poi_details,
        drive_metrics=drive_metrics,
    )

    first_zone = result[result["Минут_пешком"].eq(5)].iloc[0]

    assert first_zone["Остановочных_комплексов"] == 0
    assert first_zone["Остановочная_доступность_из_10"] == 0.0
    assert first_zone["Авто_доступность_до_центра_из_10"] == 10.0