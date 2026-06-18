from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def plot_categories_chart(summary: pd.DataFrame, output_path: Path) -> None:
    """Строит график топ-категорий POI."""
    fig, ax = plt.subplots(figsize=(11, 6))

    if summary is not None and not summary.empty:
        data = summary.copy()

        if "category" not in data.columns:
            data["category"] = "Прочее"

        if "count" not in data.columns:
            data["count"] = 0

        data["count"] = pd.to_numeric(data["count"], errors="coerce").fillna(0)
        data = data.sort_values("count", ascending=False).head(12).sort_values("count", ascending=True)

        ax.barh(data["category"], data["count"])

    ax.set_title("Топ категорий POI")
    ax.set_xlabel("Количество объектов")
    ax.set_ylabel("")

    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_quality_chart(scores: pd.DataFrame, output_path: Path) -> None:
    """Строит график качества среды в шкале 0–10."""
    fig, ax = plt.subplots(figsize=(11, 6))

    if scores is not None and not scores.empty:
        data = scores.copy()

        if "metric" not in data.columns:
            data["metric"] = "Метрика"

        if "score" not in data.columns:
            data["score"] = 0

        data["score"] = pd.to_numeric(data["score"], errors="coerce").fillna(0).clip(0, 10)
        data = data.sort_values("score", ascending=True)

        ax.barh(data["metric"], data["score"])

    ax.set_title("Индексы качества локации")
    ax.set_xlabel("Баллы, 0–10")
    ax.set_ylabel("")
    ax.set_xlim(0, 10)

    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)