from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


def export_quality_line_chart(
    *,
    quality_scores: pd.DataFrame | list[dict[str, Any]] | None,
    output_path: Path,
) -> Path | None:
    """Строит линейный график по качеству локации."""
    df = _as_df(quality_scores)

    if df.empty:
        return None

    metric_col = _first_existing_column(df, ["Метрика", "metric", "Показатель"])
    score_col = _first_existing_column(df, ["Оценка_из_10", "Оценка_из_100", "score"])

    if not metric_col or not score_col:
        return None

    data = df.copy()
    data["_score"] = pd.to_numeric(data[score_col], errors="coerce")

    if score_col == "Оценка_из_100":
        data["_score"] = data["_score"] / 10

    data = data.dropna(subset=["_score"])

    if data.empty:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(range(len(data)), data["_score"], marker="o")
    ax.set_title("Качество локации по метрикам")
    ax.set_ylabel("Оценка из 10")
    ax.set_ylim(0, 10)
    ax.set_xticks(range(len(data)))
    ax.set_xticklabels(data[metric_col].astype(str), rotation=35, ha="right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)

    return output_path


def export_top_categories_line_chart(
    *,
    category_summary: pd.DataFrame | list[dict[str, Any]] | None,
    output_path: Path,
    top_n: int = 12,
) -> Path | None:
    """Строит линейный график по топу категорий."""
    df = _as_df(category_summary)

    if df.empty:
        return None

    category_col = _first_existing_column(df, ["Категория", "category", "Функция", "Название"])
    count_col = _first_existing_column(df, ["Количество", "count", "POI", "Количество_POI"])

    if not category_col or not count_col:
        return None

    data = df.copy()
    data["_count"] = pd.to_numeric(data[count_col], errors="coerce").fillna(0)
    data = data.sort_values("_count", ascending=False).head(top_n)

    if data.empty:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(range(len(data)), data["_count"], marker="o")
    ax.set_title("Топ категорий по количеству POI")
    ax.set_ylabel("Количество POI")
    ax.set_xticks(range(len(data)))
    ax.set_xticklabels(data[category_col].astype(str), rotation=35, ha="right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)

    return output_path


def _as_df(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()

    if isinstance(value, pd.DataFrame):
        return value.copy()

    if isinstance(value, list):
        return pd.DataFrame(value)

    return pd.DataFrame()


def _first_existing_column(df: pd.DataFrame, columns: list[str]) -> str | None:
    for column in columns:
        if column in df.columns:
            return column

    return None