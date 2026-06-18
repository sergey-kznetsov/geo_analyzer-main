from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings

logger = get_logger("geo_analyzer.dgis.category_catalog")

CATEGORY_CATALOG_VERSION = "dgis_category_catalog_v4_region_manual_refresh"


@dataclass(frozen=True, slots=True)
class RegionRef:
    id: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class RubricRef:
    id: str
    name: str
    title: str
    caption: str
    alias: str
    keyword: str
    parent_id: str
    type: str
    region_id: str

    @property
    def display_name(self) -> str:
        return self.title or self.caption or self.name or self.keyword or self.alias or self.id


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "да", "on"}


def refresh_dgis_catalog_requested() -> bool:
    """Return True only when the rubric catalog must be refreshed manually.

    This deliberately ignores ``GEO_ANALYZER_REFRESH_CACHE``. The analysis cache
    and the official 2GIS regional catalog cache are independent.
    """
    env_value = os.getenv("GEO_ANALYZER_REFRESH_DGIS_CATALOG")
    if env_value is not None:
        return _truthy(env_value)
    try:
        settings = get_settings()
        dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
        return _truthy(dgis_config.get("refresh_catalog", False))
    except Exception:
        return False


def normalize_category_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("ё", "е").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]
    if isinstance(data.get("items"), list):
        return data["items"]
    return []


def _meta_error(data: dict[str, Any] | None, status_code: int | None = None) -> tuple[int | None, str, str]:
    if not isinstance(data, dict):
        return status_code, "", ""
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    try:
        code = int(meta.get("code")) if meta.get("code") is not None else status_code
    except (TypeError, ValueError):
        code = status_code
    error = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    return code, str(error.get("type") or ""), str(error.get("message") or "")


def _raise_if_auth_error(data: dict[str, Any] | None, *, status_code: int | None = None, stage: str) -> None:
    code, error_type, message = _meta_error(data, status_code=status_code)
    haystack = f"{error_type} {message}".lower()
    if code in {401, 403} and ("key" in haystack or "authorization" in haystack or "api" in haystack):
        raise RuntimeError(
            "2GIS authorization failed: "
            f"stage={stage}; code={code}; type={error_type}; message={message}. "
            "Проверь DGIS_API_KEY."
        )


def get_region_for_point(latitude: float | None, longitude: float | None) -> RegionRef:
    """Resolve the actual 2GIS region for the analysed point.

    The fallback region from config is used only when there are no coordinates,
    API calls are disabled, or the API key is absent. When 2GIS returns an
    authorization error, the analysis must stop instead of silently falling back
    to a different city.
    """
    settings = get_settings()
    fallback = RegionRef(id=settings.dgis_region_id or "", name="config_fallback")

    if latitude is None or longitude is None or settings.no_api or not settings.dgis_api_key:
        return fallback

    url = f"{settings.dgis_catalog_url.rstrip('/')}/2.0/region/get"
    params = {
        "point": f"{float(longitude)},{float(latitude)}",
        "key": settings.dgis_api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=settings.dgis_timeout)
        data = response.json()
        _raise_if_auth_error(data if isinstance(data, dict) else {}, status_code=response.status_code, stage="region/get")
        items = _extract_items(data if isinstance(data, dict) else {})
        if items:
            first = items[0]
            region_id = str(first.get("id", "") or "").strip()
            if region_id:
                return RegionRef(id=region_id, name=str(first.get("name", "") or ""))

        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            region_id = str(result.get("id", "") or "").strip()
            if region_id:
                return RegionRef(id=region_id, name=str(result.get("name", "") or ""))
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("2GIS region/get failed for point=%s,%s: %s", latitude, longitude, exc)

    return fallback


def _catalog_cache_path(region_id: str, locale: str) -> Path:
    settings = get_settings()
    safe_region = re.sub(r"[^0-9a-zA-Z_-]+", "_", str(region_id or "unknown"))
    safe_locale = re.sub(r"[^0-9a-zA-Z_-]+", "_", str(locale or "ru_RU"))
    return settings.cache_dir / "dgis_category_catalog" / f"{CATEGORY_CATALOG_VERSION}_{safe_region}_{safe_locale}.json"


def _load_cached_catalog(region_id: str, locale: str) -> dict[str, Any] | None:
    path = _catalog_cache_path(region_id, locale)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Не удалось прочитать кеш рубрик 2GIS %s: %s", path, exc)
        return None

    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None

    metadata = data.get("metadata", {})
    if isinstance(metadata, dict) and str(metadata.get("region_id", "")) != str(region_id):
        return None

    return data


def _save_cached_catalog(region_id: str, locale: str, data: dict[str, Any]) -> Path:
    path = _catalog_cache_path(region_id, locale)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def fetch_category_catalog_for_region(*, region_id: str, locale: str = "ru_RU") -> dict[str, Any]:
    settings = get_settings()
    if not region_id:
        raise RuntimeError("Не определён region_id 2GIS. Нельзя получить рубрикатор.")
    if settings.no_api:
        raise RuntimeError("Включён --no-api, но кеш рубрик для региона не найден.")
    if not settings.dgis_api_key:
        raise RuntimeError("Не найден DGIS_API_KEY. Нельзя получить рубрикатор 2GIS.")

    url = f"{settings.dgis_catalog_url.rstrip('/')}/2.0/catalog/rubric/list"
    page = 1
    page_size = 10000
    raw_items: list[dict[str, Any]] = []
    total: int | None = None

    while True:
        params = {
            "key": settings.dgis_api_key,
            "region_id": str(region_id),
            "locale": locale,
            "page": page,
            "page_size": page_size,
            "fields": "items.rubrics",
        }

        response = requests.get(url, params=params, timeout=settings.dgis_timeout)
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise

        _raise_if_auth_error(payload if isinstance(payload, dict) else {}, status_code=response.status_code, stage="catalog/rubric/list")
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        code = int(meta.get("code", response.status_code))
        if code >= 400:
            raise RuntimeError(
                "2GIS rubric/list вернул ошибку: "
                f"code={code}, response={json.dumps(payload, ensure_ascii=False)[:1000]}"
            )

        response.raise_for_status()
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        items = result.get("items", [])
        if not isinstance(items, list):
            items = []
        raw_items.extend(item for item in items if isinstance(item, dict))

        try:
            total = int(result.get("total"))
        except (TypeError, ValueError):
            total = None

        if total is not None and len(raw_items) >= total:
            break
        if not items or len(items) < page_size:
            break
        page += 1
        time.sleep(0.15)

    flat_items = flatten_category_items(raw_items, region_id=str(region_id))
    return {
        "metadata": {
            "source": "2GIS Categories API",
            "endpoint": "/2.0/catalog/rubric/list",
            "region_id": str(region_id),
            "locale": locale,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw_groups_count": len(raw_items),
            "items_count": len(flat_items),
            "version": CATEGORY_CATALOG_VERSION,
            "refresh_mode": "manual",
        },
        "items": flat_items,
    }


def flatten_category_items(items: list[dict[str, Any]], *, region_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any], parent_id: str = "") -> None:
        rubric_id = str(item.get("id", "") or "").strip()
        if not rubric_id:
            return
        if rubric_id not in seen:
            seen.add(rubric_id)
            result.append(
                {
                    "id": rubric_id,
                    "type": str(item.get("type", "") or "").strip(),
                    "region_id": str(item.get("region_id", "") or region_id or "").strip(),
                    "parent_id": str(item.get("parent_id", "") or parent_id or "").strip(),
                    "name": str(item.get("name", "") or "").strip(),
                    "title": str(item.get("title", "") or "").strip(),
                    "caption": str(item.get("caption", "") or "").strip(),
                    "keyword": str(item.get("keyword", "") or "").strip(),
                    "alias": str(item.get("alias", "") or "").strip(),
                    "seo_name": str(item.get("seo_name", "") or "").strip(),
                    "branch_count": item.get("branch_count"),
                    "org_count": item.get("org_count"),
                    "geo_count": item.get("geo_count"),
                }
            )
        children = item.get("rubrics") or []
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    add(child, parent_id=rubric_id)

    for item in items:
        if isinstance(item, dict):
            add(item)

    result.sort(key=lambda row: normalize_category_name(row.get("title") or row.get("caption") or row.get("name") or row.get("keyword") or row.get("alias")))
    return result


def load_or_fetch_category_catalog(*, region_id: str, locale: str = "ru_RU", refresh: bool = False) -> dict[str, Any]:
    """Load regional rubric catalog from cache or 2GIS.

    ``refresh`` is kept for backward compatibility, but generic analysis refresh
    must not refresh the catalog. The catalog is forcibly reloaded only when
    ``refresh_dgis_catalog_requested()`` is true.
    """
    force_refresh = refresh_dgis_catalog_requested()
    if not force_refresh:
        cached = _load_cached_catalog(region_id, locale)
        if cached and cached.get("items"):
            return cached

    catalog = fetch_category_catalog_for_region(region_id=region_id, locale=locale)
    path = _save_cached_catalog(region_id, locale, catalog)
    logger.info(
        "Каталог рубрик 2GIS сохранён region_id=%s path=%s items=%s force_refresh=%s",
        region_id,
        path,
        len(catalog.get("items", [])),
        force_refresh,
    )
    return catalog


def _rubric_from_row(row: dict[str, Any]) -> RubricRef:
    return RubricRef(
        id=str(row.get("id", "") or "").strip(),
        name=str(row.get("name", "") or "").strip(),
        title=str(row.get("title", "") or "").strip(),
        caption=str(row.get("caption", "") or "").strip(),
        alias=str(row.get("alias", "") or "").strip(),
        keyword=str(row.get("keyword", "") or "").strip(),
        parent_id=str(row.get("parent_id", "") or "").strip(),
        type=str(row.get("type", "") or "").strip(),
        region_id=str(row.get("region_id", "") or "").strip(),
    )


def build_catalog_indexes(catalog: dict[str, Any]) -> tuple[dict[str, RubricRef], dict[str, list[RubricRef]]]:
    by_id: dict[str, RubricRef] = {}
    by_name: dict[str, list[RubricRef]] = {}
    items = catalog.get("items", [])
    if not isinstance(items, list):
        items = []

    for row in items:
        if not isinstance(row, dict):
            continue
        rubric = _rubric_from_row(row)
        if not rubric.id:
            continue
        by_id[rubric.id] = rubric
        for value in [rubric.name, rubric.title, rubric.caption, rubric.keyword, rubric.alias]:
            key = normalize_category_name(value)
            if not key:
                continue
            by_name.setdefault(key, [])
            if rubric not in by_name[key]:
                by_name[key].append(rubric)
    return by_id, by_name


def _choose_best_rubric(candidates: list[RubricRef]) -> RubricRef | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            0 if item.type == "rubric" else 1,
            1 if not item.parent_id else 0,
            0 if item.region_id else 1,
            item.display_name,
        ),
    )[0]


def resolve_rubric_from_catalog(*, name: str, catalog: dict[str, Any], fallback_id: str | None = None) -> RubricRef | None:
    target = normalize_category_name(name)
    if not target and not fallback_id:
        return None

    by_id, by_name = build_catalog_indexes(catalog)
    if fallback_id:
        by_id_match = by_id.get(str(fallback_id).strip())
        if by_id_match:
            return by_id_match
    if target in by_name:
        return _choose_best_rubric(by_name[target])

    contains_candidates: list[RubricRef] = []
    for key, rubrics in by_name.items():
        if target and (target in key or key in target):
            contains_candidates.extend([rubric for rubric in rubrics if rubric.parent_id or rubric.type == "rubric"])
    return _choose_best_rubric(contains_candidates)


def resolve_configured_place_rubrics(
    entries: list[dict[str, Any]],
    *,
    latitude: float,
    longitude: float,
) -> tuple[list[dict[str, Any]], list[str], RegionRef, dict[str, Any]]:
    settings = get_settings()
    dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
    locale = str(dgis_config.get("category_catalog_locale") or "ru_RU").strip() or "ru_RU"

    region = get_region_for_point(latitude, longitude)
    if not region.id:
        raise RuntimeError("Не удалось определить region_id 2GIS для точки. Без region_id нельзя корректно подобрать rubric_id.")

    catalog = load_or_fetch_category_catalog(
        region_id=region.id,
        locale=locale,
        refresh=True,
    )

    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    used_ids: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category", "") or "").strip()
        rubric_name = str(entry.get("rubric_name", "") or category or "").strip()
        fallback_id = str(entry.get("rubric_id", "") or "").strip() or None
        rubric = resolve_rubric_from_catalog(name=rubric_name, catalog=catalog, fallback_id=fallback_id)
        if rubric is None:
            missing.append(rubric_name or category or fallback_id or "<empty>")
            continue
        if rubric.id in used_ids:
            continue
        used_ids.add(rubric.id)
        resolved.append(
            {
                "category": category or rubric.display_name,
                "rubric_name": rubric.display_name,
                "rubric_id": rubric.id,
                "region_id": region.id,
                "region_name": region.name,
                "official_name": rubric.name,
                "official_title": rubric.title,
                "official_caption": rubric.caption,
                "official_alias": rubric.alias,
                "official_keyword": rubric.keyword,
                "official_parent_id": rubric.parent_id,
                "official_type": rubric.type,
                "official_region_id": rubric.region_id,
            }
        )

    logger.info(
        "Рубрики 2GIS для точки: region_id=%s, configured=%d, resolved=%d, missing=%d, catalog_refresh_requested=%s",
        region.id,
        len(entries),
        len(resolved),
        len(missing),
        refresh_dgis_catalog_requested(),
    )
    return resolved, missing, region, catalog


def official_category_name_for_rubric_id(rubric_id: Any, catalog: dict[str, Any]) -> str | None:
    rid = str(rubric_id or "").strip()
    if not rid:
        return None
    by_id, _ = build_catalog_indexes(catalog)
    rubric = by_id.get(rid)
    return rubric.display_name if rubric else None


__all__ = [
    "CATEGORY_CATALOG_VERSION",
    "RegionRef",
    "RubricRef",
    "build_catalog_indexes",
    "fetch_category_catalog_for_region",
    "flatten_category_items",
    "get_region_for_point",
    "load_or_fetch_category_catalog",
    "normalize_category_name",
    "official_category_name_for_rubric_id",
    "refresh_dgis_catalog_requested",
    "resolve_configured_place_rubrics",
    "resolve_rubric_from_catalog",
]
