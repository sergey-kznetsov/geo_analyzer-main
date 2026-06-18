from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from geo_analyzer.core.settings import get_settings


BENCHMARK_SCHEMA_VERSION = "city_benchmark_v2_analysis_baseline"


def _slugify(value: str | None) -> str:
    text = str(value or "unknown_city").strip().lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown_city"


def _config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _is_valid_city(city: str | None) -> bool:
    if not city:
        return False

    text = str(city).strip().lower()

    if not text:
        return False

    if text in {"unknown", "unknown_city", "none", "null", "coordinates"}:
        return False

    if not re.search(r"[а-яa-z]", text):
        return False

    return True


def _get_benchmark_root() -> Path:
    settings = get_settings()

    benchmark_dir = (
        getattr(settings, "benchmark_dir", None)
        or getattr(settings, "benchmarks_dir", None)
        or None
    )

    if benchmark_dir:
        return Path(benchmark_dir)

    data_dir = getattr(settings, "data_dir", Path("data"))
    return Path(data_dir) / "benchmarks"


def _benchmark_dir(city: str | None) -> Path:
    return _get_benchmark_root() / _slugify(city)


def _meta_path(city: str | None) -> Path:
    return _benchmark_dir(city) / "meta.json"


def _summary_path(city: str | None) -> Path:
    return _benchmark_dir(city) / "benchmark_summary.parquet"


def _history_path(city: str | None) -> Path:
    return _benchmark_dir(city) / "history.parquet"


def _normalize_quality_scores(quality_scores: pd.DataFrame | None) -> pd.DataFrame:
    columns = [
        "Метрика",
        "Бенчмарк_города_из_10",
        "Пояснение",
        "Шкала_оценки",
    ]

    if quality_scores is None or quality_scores.empty:
        return pd.DataFrame(columns=columns)

    data = quality_scores.copy()

    if "Оценка_из_10" not in data.columns and "Оценка_из_100" in data.columns:
        data["Оценка_из_10"] = (
            pd.to_numeric(data["Оценка_из_100"], errors="coerce").fillna(0) / 10
        )

    if "Оценка_из_10" not in data.columns:
        data["Оценка_из_10"] = 0

    data["Оценка_из_10"] = (
        pd.to_numeric(data["Оценка_из_10"], errors="coerce")
        .fillna(0)
        .clip(0, 10)
        .round(2)
    )

    for column in ["Метрика", "Пояснение", "Шкала_оценки"]:
        if column not in data.columns:
            data[column] = pd.NA

    result = data[["Метрика", "Оценка_из_10", "Пояснение", "Шкала_оценки"]].copy()
    result = result.rename(columns={"Оценка_из_10": "Бенчмарк_города_из_10"})

    return result[columns]


def _build_history_rows(
    city: str,
    quality_scores: pd.DataFrame,
    *,
    source_address: str | None,
    parameters: dict[str, Any] | None,
) -> pd.DataFrame:
    if quality_scores is None or quality_scores.empty:
        return pd.DataFrame()

    data = quality_scores.copy()

    if "Оценка_из_10" not in data.columns and "Оценка_из_100" in data.columns:
        data["Оценка_из_10"] = (
            pd.to_numeric(data["Оценка_из_100"], errors="coerce").fillna(0) / 10
        )

    if "Оценка_из_10" not in data.columns:
        data["Оценка_из_10"] = 0

    data["Оценка_из_10"] = (
        pd.to_numeric(data["Оценка_из_10"], errors="coerce")
        .fillna(0)
        .clip(0, 10)
        .round(2)
    )

    now = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []

    for _, row in data.iterrows():
        rows.append(
            {
                "created_at": now,
                "city": city,
                "source_address": source_address,
                "metric": row.get("Метрика"),
                "score_10": row.get("Оценка_из_10"),
                "parameters": json.dumps(parameters or {}, ensure_ascii=False),
            }
        )

    return pd.DataFrame(rows)


def load_city_benchmark(city: str | None) -> dict[str, Any] | None:
    settings = get_settings()

    if not _is_valid_city(city):
        return None

    meta_path = _meta_path(city)
    summary_path = _summary_path(city)

    if not meta_path.exists() or not summary_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        summary = pd.read_parquet(summary_path)
    except Exception:
        return None

    if meta.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        return None

    if meta.get("config_hash") != _config_hash(getattr(settings, "config", {}) or {}):
        return None

    return {
        "meta": meta,
        "summary": summary,
        "source": "local_city_benchmark_cache",
        "path": str(_benchmark_dir(city)),
    }


def save_city_benchmark(
    city: str,
    quality_scores: pd.DataFrame,
    *,
    source_address: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    benchmark_dir = _benchmark_dir(city)
    benchmark_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().isoformat(timespec="seconds")
    summary = _normalize_quality_scores(quality_scores)

    meta = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "city": city,
        "city_slug": benchmark_dir.name,
        "created_at": now,
        "updated_at": now,
        "source": "first_successful_analysis_baseline",
        "source_address": source_address,
        "description": (
            "Рабочий городской benchmark: первый успешный анализ по городу "
            "сохраняется как базовый уровень города. Следующие адреса этого "
            "города сравниваются с сохранённым уровнем. Обновление выполняется "
            "вручную через refresh_city_benchmark."
        ),
        "parameters": parameters or {},
        "config_hash": _config_hash(getattr(settings, "config", {}) or {}),
    }

    summary.to_parquet(_summary_path(city), index=False)

    _meta_path(city).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    history_new = _build_history_rows(
        city,
        quality_scores,
        source_address=source_address,
        parameters=parameters,
    )

    history_path = _history_path(city)

    if history_path.exists():
        try:
            history_old = pd.read_parquet(history_path)
            history = pd.concat([history_old, history_new], ignore_index=True)
        except Exception:
            history = history_new
    else:
        history = history_new

    if not history.empty:
        history.to_parquet(history_path, index=False)

    return {
        "meta": meta,
        "summary": summary,
        "source": "saved_city_benchmark_cache",
        "path": str(benchmark_dir),
    }


def get_or_create_city_benchmark(
    city: str | None,
    quality_scores: pd.DataFrame,
    *,
    force_refresh: bool = False,
    source_address: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _is_valid_city(city):
        return {
            "meta": {
                "schema_version": BENCHMARK_SCHEMA_VERSION,
                "city": city,
                "source": "skipped_invalid_city",
                "description": "Город не определён, benchmark не создан.",
            },
            "summary": pd.DataFrame(
                columns=[
                    "Метрика",
                    "Бенчмарк_города_из_10",
                    "Пояснение",
                    "Шкала_оценки",
                ]
            ),
            "source": "skipped_invalid_city",
            "path": None,
        }

    city_clean = str(city).strip()

    if not force_refresh:
        cached = load_city_benchmark(city_clean)

        if cached is not None:
            return cached

    return save_city_benchmark(
        city_clean,
        quality_scores,
        source_address=source_address,
        parameters=parameters,
    )