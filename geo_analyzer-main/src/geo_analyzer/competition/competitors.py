"""Подсистема конкурентного анализа рынка жилья.

Модуль работает через официальный рубрикатор 2GIS текущего региона:
координаты точки → region_id → rubric/list → rubric_id → Places API.

Текстовый q-search для поиска конкурентов не используется.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.category_catalog import (
    build_catalog_indexes,
    get_region_for_point,
    load_or_fetch_category_catalog,
    normalize_category_name,
    official_category_name_for_rubric_id,
)

logger = get_logger("geo_analyzer.competition.competitors")

COMPETITION_LOADER_VERSION = "competition_v3_runtime_region_official_rubrics"
SEMANTIC_PAGE_SIZE = 10
SEMANTIC_RUBRIC_MAX_PAGES = 50

RECENT_BUILT_YEARS = 3
DEFAULT_UNITS_PER_COMPLEX = 150

# Эти маркеры используются только внутри официального rubric/list текущего региона.
# Это не q-search в Places API.
COMPETITION_RUBRIC_MARKERS = (
    "жилой комплекс",
    "жилые комплексы",
    "новострой",
    "новостройки",
    "строящ",
    "строительство жил",
    "многоквартир",
    "жилые дома",
    "жилой дом",
    "застройщик",
    "квартиры в новострой",
    "квартиры от застройщика",
    "апартаменты",
)

COMPETITION_RUBRIC_NEGATIVE_MARKERS = (
    "общежит",
    "гостиниц",
    "отел",
    "санатор",
    "интернат",
    "пансионат",
    "ремонт",
    "мебел",
    "коммуналь",
    "гараж",
    "парков",
)

COMPLEX_POSITIVE_MARKERS = {
    "жилой комплекс",
    "жк ",
    "жк,",
    "жк.",
    "новострой",
    "новый дом",
    "строящ",
    "строительство",
    "сдача",
    "сдан",
    "очередь",
    "клубный дом",
    "апарт",
    "апартаменты",
    "застройщик",
    "квартиры от застройщика",
}

COMPLEX_NEGATIVE_MARKERS = {
    "магазин",
    "офис продаж мебели",
    "ремонт",
    "автосервис",
    "парков",
    "общежит",
    "учебно",
    "колледж",
    "техникум",
    "университет",
    "институт",
    "больниц",
    "санатор",
    "интернат",
}

INSTITUTIONAL_ABSOLUTE_EXCLUDE_MARKERS = {
    "учебно-жилой",
    "учебно жилой",
    "колледж",
    "техникум",
    "университет",
    "институт",
    "общежитие",
    "общежит",
}

UNDER_CONSTRUCTION_MARKERS = {
    "строящ",
    "ведется строительство",
    "ведётся строительство",
    "строительство",
    "котлован",
    "старт продаж",
    "бронирование",
    "сдача",
    "проектная декларация",
}

DELIVERED_MARKERS = {
    "сдан",
    "сданный",
    "дом сдан",
    "введен в эксплуатацию",
    "введён в эксплуатацию",
    "готовый дом",
    "ключи",
}

DISTANCE_BANDS = [
    (0, 500, "0–500 м"),
    (500, 1000, "500–1000 м"),
    (1000, 3000, "1000–3000 м"),
    (3000, 10_000, "3000+ м"),
]

EARTH_METERS_PER_DEG_LAT = 111_320.0


@dataclass(slots=True)
class CompetitionResult:
    summary: pd.DataFrame
    competitors: pd.DataFrame
    developers: pd.DataFrame
    text_summary: str
    gui_label: str
    benchmark_context: dict[str, Any] = field(default_factory=dict)


def analyze_competition(
    *,
    pois: pd.DataFrame | list[dict[str, Any]] | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_m: int | None = None,
) -> CompetitionResult:
    settings = get_settings()
    radius = int(radius_m or settings.poi_radius_m)

    own = _load_competitors(latitude, longitude, radius)
    provided = _to_dataframe(pois)

    # Кеш основного POI можно использовать как дополнительный источник,
    # но собственная загрузка конкурентов всегда идёт через 2GIS API и rubric_id.
    cached = _load_from_places_cache()

    combined = _concat([provided, own, cached])
    combined = _filter_competitors(combined)
    combined = _deduplicate(combined)

    if combined.empty:
        return _empty_result("Конкурентный анализ: 2GIS не дал новостроек, строящихся или недавно сданных жилых объектов рядом.")

    details = _build_competitor_details(combined, latitude, longitude)
    details = _filter_relevant_details(details)

    if details.empty:
        return _empty_result("Конкурентный анализ: объекты найдены, но среди них нет новостроек, строящихся или недавно сданных жилых объектов.")

    summary = _build_summary(details)
    developers = _build_developers(details)
    benchmark_context = _load_benchmark_context()
    text_summary, gui_label = _build_text_outputs(details, developers, benchmark_context)
    return CompetitionResult(summary, details, developers, text_summary, gui_label, benchmark_context)


def _cache_path(latitude: float, longitude: float, radius_m: int, region_id: str) -> Path:
    settings = get_settings()
    payload = {
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
        "radius_m": int(radius_m),
        "region_id": str(region_id),
        "version": COMPETITION_LOADER_VERSION,
    }
    digest = hashlib.md5(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return settings.cache_dir / "competition" / f"{digest}.json"


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]
    if isinstance(data.get("items"), list):
        return data["items"]
    return []


def _extract_total(data: dict[str, Any]) -> int | None:
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("total", "total_count", "items_count"):
            try:
                value = result.get(key)
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _fetch_2gis_items(url: str, base_params: dict[str, Any], max_pages: int, timeout: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    total: int | None = None

    for page in range(1, max_pages + 1):
        params = dict(base_params, page=page)
        items: list[dict[str, Any]] | None = None
        raw_data: dict[str, Any] = {}

        for attempt in range(3):
            try:
                response = requests.get(url, params=params, timeout=timeout)
                data = response.json()
                raw_data = data if isinstance(data, dict) else {}

                meta = raw_data.get("meta", {})
                code = int(meta.get("code", response.status_code))

                if code == 429 or code >= 500:
                    time.sleep(0.5 * (attempt + 1))
                    continue

                if code >= 400:
                    logger.warning(
                        "2GIS competition query error params=%s raw=%s",
                        {key: value for key, value in params.items() if key != "key"},
                        json.dumps(raw_data, ensure_ascii=False)[:600],
                    )
                    return collected

                response.raise_for_status()
                items = _extract_items(raw_data)
                if total is None:
                    total = _extract_total(raw_data)
                break
            except Exception as exc:
                logger.warning("2GIS competition query failed page=%s attempt=%s: %s", page, attempt, exc)
                time.sleep(0.3 * (attempt + 1))

        if not items:
            break

        collected.extend(items)

        if total is not None and len(collected) >= total:
            break

        if len(items) < int(base_params.get("page_size", SEMANTIC_PAGE_SIZE)):
            break

        time.sleep(0.12)

    if total is not None and len(collected) < total:
        logger.warning(
            "2GIS competition loader: собрано %s из total=%s. Увеличь SEMANTIC_RUBRIC_MAX_PAGES или радиус/лимиты.",
            len(collected),
            total,
        )

    return collected


def _rubric_text(rubric: Any) -> str:
    parts: list[str] = []
    for attr in ("display_name", "name", "title", "caption", "alias", "keyword", "id"):
        try:
            value = getattr(rubric, attr)
        except Exception:
            value = None
        if value:
            parts.append(str(value))
    return normalize_category_name(" ".join(parts))


def _rubric_is_competition_candidate(rubric: Any) -> bool:
    text = _rubric_text(rubric)
    if not text:
        return False
    if any(marker in text for marker in COMPETITION_RUBRIC_NEGATIVE_MARKERS):
        return False
    return any(marker in text for marker in COMPETITION_RUBRIC_MARKERS)


def _competition_rubrics(catalog: dict[str, Any]) -> list[Any]:
    by_id, _by_name = build_catalog_indexes(catalog)
    result: list[Any] = []
    seen: set[str] = set()

    for rubric in by_id.values():
        if not _rubric_is_competition_candidate(rubric):
            continue
        if rubric.id in seen:
            continue
        seen.add(rubric.id)
        result.append(rubric)

    return sorted(result, key=lambda item: item.display_name)


def _load_competitors(latitude: float | None, longitude: float | None, radius_m: int) -> pd.DataFrame:
    if latitude is None or longitude is None:
        return pd.DataFrame()

    settings = get_settings()

    if settings.no_api:
        return pd.DataFrame()

    region = get_region_for_point(float(latitude), float(longitude))
    if not region.id:
        logger.warning("2GIS competition loader: не удалось определить region_id для точки.")
        return pd.DataFrame()

    path = _cache_path(float(latitude), float(longitude), radius_m, region.id)
    if settings.use_cache and not settings.refresh_cache and path.exists():
        try:
            return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass

    dgis_config = settings.config.get("dgis", {}) if isinstance(settings.config, dict) else {}
    locale = str(dgis_config.get("category_catalog_locale") or "ru_RU").strip() or "ru_RU"
    catalog = load_or_fetch_category_catalog(
        region_id=region.id,
        locale=locale,
        refresh=bool(settings.refresh_cache),
    )
    rubrics = _competition_rubrics(catalog)

    if not rubrics:
        logger.warning(
            "2GIS competition loader: в rubric/list region_id=%s не найдены рубрики новостроек/ЖК/строящихся объектов.",
            region.id,
        )
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    url = f"{settings.dgis_catalog_url.rstrip('/')}/3.0/items"
    page_size = min(int(getattr(settings, "dgis_places_page_size", SEMANTIC_PAGE_SIZE)), 10)
    max_pages = int(getattr(settings, "dgis_places_max_pages", SEMANTIC_RUBRIC_MAX_PAGES) or SEMANTIC_RUBRIC_MAX_PAGES)
    max_pages = max(max_pages, SEMANTIC_RUBRIC_MAX_PAGES)

    fields = (
        "items.id,items.external_id,items.point,items.address_name,items.full_address_name,"
        "items.rubrics,items.name,items.attribute_groups,items.context,items.schedule"
    )
    point = f"{float(longitude)},{float(latitude)}"
    common = {
        "region_id": region.id,
        "point": point,
        "location": point,
        "radius": int(radius_m),
        "page_size": page_size,
        "fields": fields,
        "key": settings.dgis_api_key,
        "sort": "distance",
    }

    def absorb(items: list[dict[str, Any]], rubric_id: str, rubric_label: str) -> None:
        for item in items:
            item_point = item.get("point") or {}
            item_rubrics = item.get("rubrics") or []
            rubric_ids = [str(r.get("id")) for r in item_rubrics if isinstance(r, dict) and r.get("id")]

            rubric_names: list[str] = []
            for rid in rubric_ids:
                official = official_category_name_for_rubric_id(rid, catalog)
                if official and official not in rubric_names:
                    rubric_names.append(official)

            for r in item_rubrics:
                if isinstance(r, dict) and r.get("name") and str(r.get("name")) not in rubric_names:
                    rubric_names.append(str(r.get("name")))

            if not rubric_names:
                rubric_names = [rubric_label]

            rows.append(
                {
                    "dgis_id": item.get("id"),
                    "fid": item.get("external_id"),
                    "Название": item.get("name"),
                    "Адрес": item.get("address_name") or item.get("full_address_name"),
                    "Категория_2GIS": rubric_names[0],
                    "Категория_2GIS_официальная": rubric_names[0],
                    "rubrics_2gis": rubric_names,
                    "source_categories_2gis": rubric_names,
                    "category_groups_2gis": rubric_ids or [rubric_id],
                    "rubric_id": rubric_id,
                    "resolved_region_id": region.id,
                    "Поисковый_запрос": f"rubric:{rubric_id}",
                    "Широта": item_point.get("lat"),
                    "Долгота": item_point.get("lon"),
                    "attribute_groups": item.get("attribute_groups"),
                    "context": item.get("context"),
                    "raw_2gis": item,
                }
            )

    for rubric in rubrics:
        rubric_id = str(rubric.id)
        params = dict(common, rubric_id=rubric_id)
        items = _fetch_2gis_items(url, params, max_pages, settings.dgis_timeout)
        absorb(items, rubric_id, rubric.display_name)
        logger.info(
            "2GIS competition loader: region_id=%s rubric_id=%s rubric=%s items=%d",
            region.id,
            rubric_id,
            rubric.display_name,
            len(items),
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = _deduplicate(df)

    if settings.use_cache:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(df.to_dict("records"), ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            pass

    return df


def _load_from_places_cache() -> pd.DataFrame:
    settings = get_settings()
    if not settings.use_cache:
        return pd.DataFrame()

    cache_dir = settings.cache_dir / "places"
    if not cache_dir.exists():
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for path in cache_dir.glob("*.parquet"):
        try:
            frames.append(pd.read_parquet(path))
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    data = _concat(frames)
    return _filter_competitors(data)


def _load_benchmark_context() -> dict[str, Any]:
    settings = get_settings()
    bench_dir = settings.benchmark_dir
    if not bench_dir.exists():
        return {}

    snapshots: list[dict[str, Any]] = []
    for path in bench_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                snapshots.append(data)
        except Exception:
            continue

    return {"snapshots": len(snapshots)} if snapshots else {}


def _build_competitor_details(df: pd.DataFrame, latitude: float | None, longitude: float | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for _, item in df.iterrows():
        lat, lon = _coords(item)
        distance = _distance_m(latitude, longitude, lat, lon)
        units, units_estimated = _extract_units(item)
        built_year = _extract_built_year(item)
        status = _competitor_status(item, built_year)
        is_new = status in {"Строится", "Недавно сдан", "Новостройка", "Планируется/старт продаж"}

        rows.append(
            {
                "Название": _pick(item, ["Название", "name"]),
                "Адрес": _pick(item, ["Адрес", "address_name", "address"]),
                "Категория_2GIS": _pick(item, ["Категория_2GIS_официальная", "Категория_2GIS", "rubric_name", "category"]),
                "Статус_объекта": status,
                "Застройщик": _extract_developer(item),
                "Квартир_оценка": units,
                "Оценка_квартир": "Да" if units_estimated else "Нет",
                "Год_постройки": built_year if built_year else pd.NA,
                "Новый_или_строящийся": "Да" if is_new else "Нет",
                "Расстояние_м": round(distance) if distance is not None else pd.NA,
                "Зона_расстояния": _distance_band(distance),
                "Широта": lat,
                "Долгота": lon,
                "dgis_id": _pick(item, ["dgis_id", "id"]),
            }
        )

    columns = [
        "Название",
        "Адрес",
        "Категория_2GIS",
        "Статус_объекта",
        "Застройщик",
        "Квартир_оценка",
        "Оценка_квартир",
        "Год_постройки",
        "Новый_или_строящийся",
        "Расстояние_м",
        "Зона_расстояния",
        "Широта",
        "Долгота",
        "dgis_id",
    ]
    return pd.DataFrame(rows, columns=columns)


def _filter_relevant_details(details: pd.DataFrame) -> pd.DataFrame:
    # Не выкидываем обычные ЖК без явного года сдачи.
    # Для API-данных выше уже идёт фильтр по официальным жилым/новостроечным рубрикам,
    # а для офлайн-тестов/ручных данных важно сохранить все релевантные ЖК.
    if details.empty:
        return details
    return details.reset_index(drop=True)


def _build_summary(details: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    band_labels = [band[2] for band in DISTANCE_BANDS] + ["Итого"]

    for label in band_labels:
        subset = details if label == "Итого" else details[details["Зона_расстояния"] == label]
        total = int(len(subset))
        new_count = int((subset["Новый_или_строящийся"] == "Да").sum()) if total else 0
        under_construction = int((subset["Статус_объекта"] == "Строится").sum()) if total else 0
        recently_delivered = int((subset["Статус_объекта"] == "Недавно сдан").sum()) if total else 0
        units = int(pd.to_numeric(subset.get("Квартир_оценка", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if total else 0
        developers = int(subset["Застройщик"].replace("", pd.NA).dropna().nunique()) if total else 0

        rows.append(
            {
                "Зона_расстояния": label,
                "Конкурентов_всего": total,
                "Новых_или_строящихся": new_count,
                "Строящихся": under_construction,
                "Недавно_сданных": recently_delivered,
                "Квартир_у_конкурентов": units,
                "Застройщиков": developers,
            }
        )

    return pd.DataFrame(rows)


def _build_developers(details: pd.DataFrame) -> pd.DataFrame:
    data = details.copy()
    data["Застройщик"] = data["Застройщик"].replace("", pd.NA)
    data = data.dropna(subset=["Застройщик"])

    if data.empty:
        return pd.DataFrame(columns=["Застройщик", "Объектов", "Квартир_оценка", "Новых_или_строящихся"])

    grouped = data.groupby("Застройщик", as_index=False).agg(
        Объектов=("Название", "count"),
        Квартир_оценка=("Квартир_оценка", lambda s: int(pd.to_numeric(s, errors="coerce").fillna(0).sum())),
        Новых_или_строящихся=("Новый_или_строящийся", lambda s: int((s == "Да").sum())),
    )
    return grouped.sort_values(["Объектов", "Квартир_оценка"], ascending=False).reset_index(drop=True)


def _build_text_outputs(details: pd.DataFrame, developers: pd.DataFrame, benchmark_context: dict[str, Any]) -> tuple[str, str]:
    total = int(len(details))
    under_construction = int((details["Статус_объекта"] == "Строится").sum())
    recently_delivered = int((details["Статус_объекта"] == "Недавно сдан").sum())
    units = int(pd.to_numeric(details.get("Квартир_оценка", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    dev_count = int(len(developers))
    top_dev = developers.iloc[0]["Застройщик"] if not developers.empty else "—"

    bench_note = ""
    if benchmark_context.get("snapshots"):
        bench_note = f" Учтены benchmark-снапшоты города: {benchmark_context['snapshots']}."

    text = (
        f"Конкурентная среда: рядом {total} новых/строящихся/недавно сданных жилых объектов "
        f"(строятся — {under_construction}, недавно сданы — {recently_delivered}), "
        f"суммарно ~{units} квартир, застройщиков — {dev_count} "
        f"(крупнейший: {top_dev}).{bench_note}"
    )
    gui_label = f"Конкуренты: {total} объектов, ~{units} квартир, застройщиков {dev_count}"
    return text, gui_label


def _empty_result(text: str) -> CompetitionResult:
    summary_columns = [
        "Зона_расстояния",
        "Конкурентов_всего",
        "Новых_или_строящихся",
        "Строящихся",
        "Недавно_сданных",
        "Квартир_у_конкурентов",
        "Застройщиков",
    ]
    detail_columns = [
        "Название",
        "Адрес",
        "Категория_2GIS",
        "Статус_объекта",
        "Застройщик",
        "Квартир_оценка",
        "Оценка_квартир",
        "Год_постройки",
        "Новый_или_строящийся",
        "Расстояние_м",
        "Зона_расстояния",
        "Широта",
        "Долгота",
        "dgis_id",
    ]
    dev_columns = ["Застройщик", "Объектов", "Квартир_оценка", "Новых_или_строящихся"]

    return CompetitionResult(
        pd.DataFrame(columns=summary_columns),
        pd.DataFrame(columns=detail_columns),
        pd.DataFrame(columns=dev_columns),
        text,
        "Конкуренты: нет данных",
        {},
    )


def _to_dataframe(value: pd.DataFrame | list[dict[str, Any]] | None) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list):
        return pd.DataFrame(value)
    return pd.DataFrame()


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _filter_competitors(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mask = df.apply(_is_competitor, axis=1)
    return df[mask].copy()


def _is_competitor(row: pd.Series) -> bool:
    text = _row_text(row)
    category_text = _category_text(row)

    # Институциональные "учебно-жилые комплексы" не являются конкурентами застройщика,
    # даже если в названии есть фраза "жилой комплекс".
    if any(marker in text for marker in INSTITUTIONAL_ABSOLUTE_EXCLUDE_MARKERS):
        return False

    if any(marker in text for marker in COMPLEX_NEGATIVE_MARKERS):
        if not any(marker in text for marker in COMPLEX_POSITIVE_MARKERS):
            return False

    if any(marker in category_text for marker in COMPETITION_RUBRIC_NEGATIVE_MARKERS):
        return False

    category_positive = any(marker in category_text for marker in COMPETITION_RUBRIC_MARKERS)
    text_positive = any(marker in text for marker in COMPLEX_POSITIVE_MARKERS)

    if category_positive or text_positive:
        return True

    built_year = _extract_built_year(row)
    if built_year and datetime.now().year - built_year <= RECENT_BUILT_YEARS:
        return True

    return False


def _deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    data = df.copy()
    for column in ["dgis_id", "Название", "Адрес", "Широта", "Долгота"]:
        if column not in data.columns:
            data[column] = pd.NA

    key = data["dgis_id"].fillna("").astype(str).str.strip()
    fallback = (
        data["Название"].fillna("").astype(str).str.lower().str.strip()
        + "|"
        + data["Адрес"].fillna("").astype(str).str.lower().str.strip()
        + "|"
        + data["Широта"].fillna("").astype(str)
        + "|"
        + data["Долгота"].fillna("").astype(str)
    )
    data["_key"] = key.where(key.ne(""), fallback)
    return data.drop_duplicates(subset=["_key"], keep="first").drop(columns=["_key"], errors="ignore").reset_index(drop=True)


def _coords(row: pd.Series) -> tuple[float | None, float | None]:
    lat = _first_value(row, ["Широта", "lat", "latitude"])
    lon = _first_value(row, ["Долгота", "lon", "lng", "longitude"])

    geom = row.get("geometry") if "geometry" in row.index else None
    if (lat is None or lon is None) and geom is not None and hasattr(geom, "y") and hasattr(geom, "x"):
        try:
            return float(geom.y), float(geom.x)
        except (TypeError, ValueError):
            pass

    raw = row.get("raw_2gis") if "raw_2gis" in row.index else None
    if (lat is None or lon is None) and isinstance(raw, dict):
        point = raw.get("point") or {}
        lat = lat if lat is not None else point.get("lat")
        lon = lon if lon is not None else point.get("lon")

    return _safe_float_or_none(lat), _safe_float_or_none(lon)


def _distance_m(lat0: float | None, lon0: float | None, lat: float | None, lon: float | None) -> float | None:
    if None in (lat0, lon0, lat, lon):
        return None

    try:
        mean_lat = math.radians((float(lat0) + float(lat)) / 2.0)
        dx = (float(lon) - float(lon0)) * EARTH_METERS_PER_DEG_LAT * math.cos(mean_lat)
        dy = (float(lat) - float(lat0)) * EARTH_METERS_PER_DEG_LAT
        return math.hypot(dx, dy)
    except (TypeError, ValueError):
        return None


def _distance_band(distance: float | None) -> str:
    if distance is None:
        return "Неизвестно"

    for low, high, label in DISTANCE_BANDS:
        if low <= distance < high:
            return label

    return DISTANCE_BANDS[-1][2]


def _extract_units(row: pd.Series) -> tuple[int, bool]:
    direct = _attr_number(
        row,
        lambda label: "квартир" in label and not any(token in label for token in ("цена", "стоимост", "руб", "млн", "ипотек")),
    )
    if direct is not None and direct > 0:
        return direct, False

    text = _row_text(row)
    match = re.search(r"(\d+)\s*(?:квартир|кв\.)", text)
    if match:
        value = int(match.group(1))
        if value > 0:
            return value, False

    return DEFAULT_UNITS_PER_COMPLEX, True


def _extract_built_year(row: pd.Series) -> int | None:
    current_year = datetime.now().year

    year = _attr_number(
        row,
        lambda label: "год" in label
        and any(marker in label for marker in ("постройки", "сдачи", "ввода", "ввод", "готовности", "завершения")),
    )
    if year and 1900 <= year <= current_year + 5:
        return year

    text = _row_text(row)
    patterns = [
        r"(?:год\s+постройки|построен|сдан|сдача|ввод(?:\s+в\s+эксплуатацию)?)\D{0,30}((?:19|20)\d{2})",
        r"((?:19|20)\d{2})\s*(?:г\.|год|года)?\s*(?:сдач|построй|ввод|готовност)",
        r"(?:i|ii|iii|iv|v|1|2|3|4)\s*квартал\D{0,20}((?:20)\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = int(match.group(1))
            if 1900 <= value <= current_year + 5:
                return value

    return None


def _competitor_status(row: pd.Series, built_year: int | None) -> str:
    text = _row_text(row)
    category_text = _category_text(row)
    current_year = datetime.now().year

    if any(marker in text for marker in UNDER_CONSTRUCTION_MARKERS):
        if not any(marker in text for marker in DELIVERED_MARKERS):
            return "Строится"

    if "старт продаж" in text or "бронирование" in text:
        return "Планируется/старт продаж"

    if built_year and built_year > current_year:
        return "Строится"

    if built_year and current_year - built_year <= RECENT_BUILT_YEARS:
        return "Недавно сдан"

    if any(marker in text for marker in DELIVERED_MARKERS):
        return "Недавно сдан" if built_year is None else "Сдан"

    if "новострой" in category_text or "квартиры в новострой" in category_text:
        return "Новостройка"

    if "жилой комплекс" in category_text or "жилые комплексы" in category_text:
        return "ЖК/жилой комплекс"

    return "Не определён"


def _extract_developer(row: pd.Series) -> str:
    developer = _attr_text(row, lambda label: "застройщик" in label or "девелопер" in label)
    if developer:
        return developer

    text = _row_text(row)
    patterns = [
        r"застройщик\D{0,20}([а-яa-z0-9 \"«»\.\-]+)",
        r"девелопер\D{0,20}([а-яa-z0-9 \"«»\.\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip(" .,:;\"«»")
            if value:
                return value[:80]

    return ""


def _attr_number(row: pd.Series, label_match) -> int | None:
    for attr in _iter_attribute_dicts(row.get("attribute_groups") if "attribute_groups" in row.index else None):
        label = _attribute_label(attr)
        if not label_match(label):
            continue
        for key in ("value", "values", "count", "number"):
            parsed = _safe_int(attr.get(key))
            if parsed is not None:
                return parsed

    raw = row.get("raw_2gis") if "raw_2gis" in row.index else None
    for attr in _iter_attribute_dicts(raw):
        label = _attribute_label(attr)
        if not label_match(label):
            continue
        for key in ("value", "values", "count", "number"):
            parsed = _safe_int(attr.get(key))
            if parsed is not None:
                return parsed

    return None


def _attr_text(row: pd.Series, label_match) -> str:
    for source in [
        row.get("attribute_groups") if "attribute_groups" in row.index else None,
        row.get("raw_2gis") if "raw_2gis" in row.index else None,
    ]:
        for attr in _iter_attribute_dicts(source):
            label = _attribute_label(attr)
            if not label_match(label):
                continue
            for key in ("value", "values", "text", "name"):
                value = attr.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, list):
                    joined = ", ".join(str(item) for item in value if str(item).strip())
                    if joined:
                        return joined[:120]
    return ""


def _attribute_label(attr: dict[str, Any]) -> str:
    return normalize_category_name(" ".join(str(attr.get(key, "")) for key in ("name", "tag", "alias", "id", "caption", "title")))


def _iter_attribute_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_attribute_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_attribute_dicts(item)


def _category_text(row: pd.Series) -> str:
    parts: list[str] = []
    for column in [
        "Категория_2GIS_официальная",
        "Категория_2GIS",
        "rubrics_2gis",
        "source_categories_2gis",
        "category_groups_2gis",
        "rubric_id",
    ]:
        if column in row.index:
            parts.append(_value_to_text(row.get(column)))
    return normalize_category_name(" ".join(parts))


def _row_text(row: pd.Series) -> str:
    parts: list[str] = []
    for value in row.values:
        if _is_missing(value):
            continue
        parts.append(_value_to_text(value))
    return normalize_category_name(" ".join(parts))


def _value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return str(value)


def _pick(row: pd.Series, columns: list[str]) -> Any:
    for column in columns:
        if column not in row.index:
            continue
        value = row.get(column)
        if _is_missing(value):
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return pd.NA


def _first_value(row: pd.Series, columns: list[str]) -> Any:
    for column in columns:
        if column not in row.index:
            continue
        value = row.get(column)
        if not _is_missing(value):
            return value
    return None


def _safe_int(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        match = re.search(r"((?:19|20)\d{2}|\d+)", str(value))
        return int(match.group(1)) if match else None


def _safe_float_or_none(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return False
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, bool) else False
