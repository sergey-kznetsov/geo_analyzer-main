from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt


def plot_analysis(result: dict, save_path: str = "output/map.png") -> None:
    """
    Простая fallback-визуализация результата.
    Поддерживает как колонку minutes, так и time_min для совместимости.
    """
    isochrones = result["isochrones"]
    pois = result["pois"]

    output = Path(save_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 10))

    iso_column = "minutes"
    if iso_column not in isochrones.columns and "time_min" in isochrones.columns:
        iso_column = "time_min"

    if not isochrones.empty:
        isochrones.plot(ax=ax, column=iso_column, alpha=0.3, legend=True)

    if not pois.empty:
        pois.plot(ax=ax, markersize=5)

    plt.title("Geo Analyzer")
    plt.tight_layout()
    plt.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)