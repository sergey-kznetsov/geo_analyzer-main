import pandas as pd

from geo_analyzer.enrichment.poi_merge import merge_raw_pois


def test_merge_raw_pois_merges_same_dgis_id_from_different_rubrics():
    data = pd.DataFrame(
        [
            {
                "dgis_id": "42",
                "fid": None,
                "Название": "Парк",
                "Адрес": "Центральная, 1",
                "Широта": 56.1,
                "Долгота": 53.1,
                "source_categories_2gis": ["Парки"],
                "rubrics_2gis": ["Парки"],
                "category_groups_2gis": ["168"],
            },
            {
                "dgis_id": "42",
                "fid": None,
                "Название": "Парк",
                "Адрес": "Центральная, 1",
                "Широта": 56.1,
                "Долгота": 53.1,
                "source_categories_2gis": ["Скверы"],
                "rubrics_2gis": ["Скверы"],
                "category_groups_2gis": ["169"],
            },
        ]
    )

    result = merge_raw_pois(data)

    assert len(result) == 1
    assert set(result.iloc[0]["source_categories_2gis"]) == {"Парки", "Скверы"}
    assert set(result.iloc[0]["category_groups_2gis"]) == {"168", "169"}


def test_merge_raw_pois_keeps_different_physical_objects():
    data = pd.DataFrame(
        [
            {"dgis_id": "1", "Название": "Школа", "Адрес": "А, 1", "Широта": 56.1, "Долгота": 53.1},
            {"dgis_id": "2", "Название": "Школа", "Адрес": "Б, 2", "Широта": 56.2, "Долгота": 53.2},
        ]
    )

    result = merge_raw_pois(data)

    assert len(result) == 2
