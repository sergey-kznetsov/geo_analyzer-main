from __future__ import annotations

from typing import Any

import pandas as pd

from geo_analyzer.core.settings import get_settings


def _level(score: float, low: float, medium: float, high: float) -> str:
    if score < low:
        return "Низкий уровень"

    if score < medium:
        return "Средний уровень"

    if score < high:
        return "Хороший уровень"

    return "Сильный уровень"


def _normalize_score(row: pd.Series) -> float:
    if "Оценка_из_10" in row.index and pd.notna(row["Оценка_из_10"]):
        return max(0.0, min(10.0, float(row["Оценка_из_10"])))

    if "Оценка_из_100" in row.index and pd.notna(row["Оценка_из_100"]):
        return max(0.0, min(10.0, float(row["Оценка_из_100"]) / 10))

    return 0.0


def _normalize_threshold(value: float) -> float:
    numeric = float(value)

    if numeric > 10:
        return numeric / 10

    return numeric


def _thresholds(raw_cfg: dict | None, fallback: dict[str, float]) -> dict[str, float]:
    cfg = raw_cfg or fallback

    return {
        "low": _normalize_threshold(float(cfg.get("low", fallback["low"]))),
        "medium": _normalize_threshold(float(cfg.get("medium", fallback["medium"]))),
        "high": _normalize_threshold(float(cfg.get("high", fallback["high"]))),
    }


def _city_benchmark_map(city_benchmark: dict[str, Any] | None) -> dict[str, float]:
    if not city_benchmark:
        return {}

    summary = city_benchmark.get("summary")

    if summary is None or not isinstance(summary, pd.DataFrame) or summary.empty:
        return {}

    if not {"Метрика", "Бенчмарк_города_из_10"}.issubset(summary.columns):
        return {}

    result: dict[str, float] = {}

    for _, row in summary.iterrows():
        metric = str(row.get("Метрика", "")).strip()

        if not metric:
            continue

        try:
            result[metric] = round(float(row.get("Бенчмарк_города_из_10", 0) or 0), 2)
        except (TypeError, ValueError):
            result[metric] = 0.0

    return result


def _city_comparison(actual_score: float, city_score: float | None) -> tuple[str, float | None]:
    if city_score is None:
        return "Нет сохранённого городского бенча", None

    delta = round(actual_score - city_score, 2)

    if delta >= 1.0:
        return f"Выше города на {delta}", delta

    if delta <= -1.0:
        return f"Ниже города на {abs(delta)}", delta

    return f"На уровне города ({delta:+.2f})", delta


def build_benchmark_summary(
    quality_scores: pd.DataFrame,
    city_benchmark: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Сравнивает текущую локацию с районными порогами и городским benchmark.

    Районный benchmark — пороговый, из config.yaml.
    Городской benchmark — сохранённый первый успешный анализ по городу
    или вручную обновлённый benchmark.
    """
    settings = get_settings()

    output_columns = [
        "Метрика",
        "Фактическая_оценка",
        "Бенчмарк_района",
        "Бенчмарк_города",
        "Бенчмарк_города_из_10",
        "Отклонение_от_города",
        "Источник_городского_бенча",
        "Пояснение",
    ]

    if quality_scores is None or quality_scores.empty:
        return pd.DataFrame(columns=output_columns)

    city_scores = _city_benchmark_map(city_benchmark)

    city_source = "нет данных"

    if city_benchmark:
        meta = city_benchmark.get("meta", {}) or {}
        city_source = (
            meta.get("source")
            or city_benchmark.get("source")
            or "нет данных"
        )

    rows: list[dict[str, object]] = []

    for _, row in quality_scores.iterrows():
        metric = str(row.get("Метрика", "")).strip()
        score = round(_normalize_score(row), 2)

        district_cfg = _thresholds(
            settings.benchmark_district.get(metric),
            {"low": 3.0, "medium": 6.0, "high": 8.0},
        )

        district_level = _level(
            score,
            district_cfg["low"],
            district_cfg["medium"],
            district_cfg["high"],
        )

        city_score = city_scores.get(metric)
        city_label, delta = _city_comparison(score, city_score)

        rows.append(
            {
                "Метрика": metric,
                "Фактическая_оценка": score,
                "Бенчмарк_района": district_level,
                "Бенчмарк_города": city_label,
                "Бенчмарк_города_из_10": city_score,
                "Отклонение_от_города": delta,
                "Источник_городского_бенча": city_source,
                "Пояснение": (
                    "Бенчмарк района — пороговая оценка из config.yaml. "
                    "Бенчмарк города — сравнение с сохранённым benchmark "
                    "для этого города."
                ),
            }
        )

    return pd.DataFrame(rows, columns=output_columns)