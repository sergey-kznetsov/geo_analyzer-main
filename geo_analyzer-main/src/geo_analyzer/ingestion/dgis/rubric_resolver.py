from __future__ import annotations

"""Резолвер рубрик 2GIS без q-search.

Модуль больше не использует ``/catalog/rubric/search`` и параметр ``q``.
Рубрики определяются только через структурный справочник региона:
``region/get`` → ``catalog/rubric/list`` → точное/внутреннее сопоставление по
уже загруженному официальному списку рубрик.
"""

import json
from pathlib import Path
from typing import Any

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.category_catalog import (
    get_region_for_point,
    load_or_fetch_category_catalog,
    normalize_category_name,
    resolve_rubric_from_catalog,
)

logger = get_logger("geo_analyzer.dgis.rubric_resolver")

RUBRIC_RESOLVER_VERSION = "rubric_resolver_v2_catalog_list_only"


def _cache_path(region_id: str) -> Path:
    settings = get_settings()
    return settings.cache_dir / "rubrics" / f"{RUBRIC_RESOLVER_VERSION}_{region_id or 'default'}.json"


def _load_cache(region_id: str) -> dict[str, str]:
    path = _cache_path(region_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    return {}


def _save_cache(region_id: str, mapping: dict[str, str]) -> None:
    settings = get_settings()
    if not settings.use_cache:
        return
    path = _cache_path(region_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _norm(value: Any) -> str:
    return normalize_category_name(value)


def get_region_id(latitude: float | None, longitude: float | None) -> str | None:
    """Определяет region_id точки только через 2GIS region/get или fallback конфига."""
    settings = get_settings()
    region = get_region_for_point(latitude, longitude)
    return region.id or settings.dgis_region_id or None


def resolve_rubric_id(name: str, *, region_id: str, fallback_id: str | None = None) -> str | None:
    """Возвращает rubric_id из официального rubric/list региона.

    Никакого API q-search здесь нет. Если fallback_id есть в rubric/list, он
    имеет приоритет. Иначе используется сопоставление имени внутри уже
    полученного официального каталога.
    """
    settings = get_settings()
    key = _norm(name) or str(fallback_id or "").strip()
    if not key:
        return fallback_id

    cache = _load_cache(region_id)
    if key in cache:
        return cache[key] or fallback_id

    if not region_id:
        return fallback_id

    dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
    locale = str(dgis_config.get("category_catalog_locale") or "ru_RU").strip() or "ru_RU"
    try:
        catalog = load_or_fetch_category_catalog(
            region_id=region_id,
            locale=locale,
            refresh=bool(settings.refresh_cache),
        )
        rubric = resolve_rubric_from_catalog(
            name=name,
            catalog=catalog,
            fallback_id=fallback_id,
        )
        resolved = rubric.id if rubric else None
    except Exception as exc:
        logger.warning("2GIS rubric/list resolver failed name=%s region_id=%s: %s", name, region_id, exc)
        resolved = None

    cache[key] = resolved or ""
    _save_cache(region_id, cache)
    return resolved or fallback_id


def resolve_place_query_rubrics(
    entries: list[dict[str, str]],
    *,
    latitude: float | None,
    longitude: float | None,
) -> list[dict[str, str]]:
    """Подставляет актуальные rubric_id через rubric/list, без rubric/search."""
    settings = get_settings()
    if not entries:
        return entries

    region_id = get_region_id(latitude, longitude) or settings.dgis_region_id
    if not region_id:
        return entries

    resolved_entries: list[dict[str, str]] = []
    changed = 0
    for entry in entries:
        name = str(entry.get("rubric_name") or entry.get("category") or "").strip()
        fallback_id = str(entry.get("rubric_id") or "").strip() or None
        rubric_id = resolve_rubric_id(name, region_id=region_id, fallback_id=fallback_id)
        new_entry = dict(entry)
        if rubric_id:
            if fallback_id and rubric_id != fallback_id:
                changed += 1
            new_entry["rubric_id"] = rubric_id
        new_entry["region_id"] = region_id
        resolved_entries.append(new_entry)

    logger.info(
        "Резолв рубрик 2GIS через rubric/list: регион=%s, записей=%d, скорректировано=%d",
        region_id,
        len(entries),
        changed,
    )
    return resolved_entries
