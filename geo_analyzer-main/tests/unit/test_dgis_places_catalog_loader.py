from geo_analyzer.ingestion.dgis.places_catalog_loader import all_catalog_rubric_entries


def test_all_catalog_rubric_entries_uses_official_catalog_not_configured_list():
    catalog = {
        "items": [
            {"id": "100", "title": "Супермаркеты", "parent_id": "", "branch_count": 10},
            {"id": "200", "title": "Неожиданная новая рубрика 2GIS", "parent_id": "", "branch_count": 3},
            {"id": "300", "title": "Пустая региональная рубрика", "parent_id": "", "branch_count": 0, "org_count": 0, "geo_count": 0},
        ]
    }

    entries = all_catalog_rubric_entries(catalog)
    ids = {entry["rubric_id"] for entry in entries}

    assert "100" in ids
    assert "200" in ids
    assert "300" not in ids
    assert any(entry["category"] == "Неожиданная новая рубрика 2GIS" for entry in entries)
