import pandas as pd

from geo_analyzer.metrics.environment_quality import build_quality_scores


def test_quality_scores_support_new_accessibility_columns():
    poi_counts = pd.DataFrame(
        [
            {
                "Минут_пешком": 5,
                "Количество": 2,
                "functional_category": "Транспорт",
                "criticality_score": 8,
                "Категория": "Остановка общественного транспорта",
            },
            {
                "Минут_пешком": 5,
                "Количество": 4,
                "functional_category": "Повседневная торговля и услуги",
                "criticality_score": 8,
                "Категория": "Супермаркет",
            },
        ]
    )

    category_summary = pd.DataFrame(
        [
            {"Категория": "Остановка общественного транспорта", "Количество": 2},
            {"Категория": "Супермаркет", "Количество": 4},
        ]
    )

    accessibility = pd.DataFrame(
        [
            {
                "Минут_пешком": 5,
                "Остановочная_доступность_из_10": 7.0,
                "Авто_доступность_до_центра_из_10": 8.5,
            },
            {
                "Минут_пешком": 10,
                "Остановочная_доступность_из_10": 5.0,
                "Авто_доступность_до_центра_из_10": 8.5,
            },
        ]
    )

    result = build_quality_scores(
        poi_counts_by_iso=poi_counts,
        category_summary=category_summary,
        network_metrics=None,
        anti_driver_penalty=0,
        accessibility_snapshot=accessibility,
    )

    transport = result[result["Метрика"].eq("Транспортная доступность")].iloc[0]

    assert transport["Оценка_из_10"] > 0
    assert transport["Оценка_из_10"] <= 10


def test_quality_scores_keep_backward_compatibility_with_old_transport_columns():
    poi_counts = pd.DataFrame(
        [
            {
                "Минут_пешком": 5,
                "Количество": 2,
                "functional_category": "Транспорт",
                "criticality_score": 8,
                "Категория": "Остановка общественного транспорта",
            }
        ]
    )

    category_summary = pd.DataFrame(
        [
            {"Категория": "Остановка общественного транспорта", "Количество": 2},
        ]
    )

    old_accessibility = pd.DataFrame(
        [
            {
                "Минут_пешком": 5,
                "Транспортная_доступность_из_10": 7.0,
            }
        ]
    )

    result = build_quality_scores(
        poi_counts_by_iso=poi_counts,
        category_summary=category_summary,
        network_metrics=None,
        anti_driver_penalty=0,
        accessibility_snapshot=old_accessibility,
    )

    transport = result[result["Метрика"].eq("Транспортная доступность")].iloc[0]

    assert transport["Оценка_из_10"] > 0
    assert transport["Оценка_из_10"] <= 10