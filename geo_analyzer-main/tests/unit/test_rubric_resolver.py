from __future__ import annotations

import geo_analyzer.ingestion.dgis.rubric_resolver as rr


class Region:
    id = "55"
    name = ""


def test_exact_title_match_wins_from_catalog(monkeypatch):
    items = [
        {"id": "1", "name": "Кофейни и кафе", "type": "rubric"},
        {"id": "2", "name": "Кафе", "type": "rubric"},
    ]
    monkeypatch.setattr(rr, "load_or_fetch_category_catalog", lambda **kwargs: {"items": items})
    assert rr.resolve_rubric_id("Кафе", region_id="55", fallback_id="999") == "2"


def test_falls_back_when_not_found(monkeypatch):
    monkeypatch.setattr(rr, "load_or_fetch_category_catalog", lambda **kwargs: {"items": []})
    assert rr.resolve_rubric_id("Несуществующая", region_id="55", fallback_id="161") == "161"


def test_resolve_place_queries_uses_region_catalog(monkeypatch):
    items = [{"id": "164", "name": "Рестораны", "type": "rubric"}]
    monkeypatch.setattr(rr, "get_region_for_point", lambda lat, lon: Region())
    monkeypatch.setattr(rr, "load_or_fetch_category_catalog", lambda **kwargs: {"items": items})
    entries = [{"category": "Рестораны", "rubric_name": "Рестораны", "rubric_id": "999"}]
    resolved = rr.resolve_place_query_rubrics(entries, latitude=56.85, longitude=53.21)
    assert resolved[0]["rubric_id"] == "164"
    assert resolved[0]["region_id"] == "55"
