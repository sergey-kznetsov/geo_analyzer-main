from __future__ import annotations

from types import SimpleNamespace

import geo_analyzer.ingestion.dgis.category_catalog as cc


def _settings(tmp_path):
    return SimpleNamespace(
        cache_dir=tmp_path,
        config={},
        no_api=False,
        dgis_api_key="test-key",
        dgis_catalog_url="https://catalog.api.2gis.com",
        dgis_timeout=1,
    )


def test_analysis_refresh_does_not_refresh_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setenv("GEO_ANALYZER_REFRESH_CACHE", "1")
    monkeypatch.delenv("GEO_ANALYZER_REFRESH_DGIS_CATALOG", raising=False)

    cached = {"metadata": {"region_id": "41"}, "items": [{"id": "350", "name": "Супермаркеты"}]}
    cc._save_cached_catalog("41", "ru_RU", cached)

    def fail_fetch(**_kwargs):
        raise AssertionError("catalog fetch must not run on generic refresh-cache")

    monkeypatch.setattr(cc, "fetch_category_catalog_for_region", fail_fetch)

    result = cc.load_or_fetch_category_catalog(region_id="41", locale="ru_RU", refresh=True)

    assert result["items"][0]["id"] == "350"


def test_manual_catalog_refresh_overrides_cached_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(cc, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setenv("GEO_ANALYZER_REFRESH_DGIS_CATALOG", "1")

    cached = {"metadata": {"region_id": "41"}, "items": [{"id": "old", "name": "Old"}]}
    cc._save_cached_catalog("41", "ru_RU", cached)

    def fetch(**_kwargs):
        return {"metadata": {"region_id": "41"}, "items": [{"id": "new", "name": "New"}]}

    monkeypatch.setattr(cc, "fetch_category_catalog_for_region", fetch)

    result = cc.load_or_fetch_category_catalog(region_id="41", locale="ru_RU", refresh=False)

    assert result["items"][0]["id"] == "new"
