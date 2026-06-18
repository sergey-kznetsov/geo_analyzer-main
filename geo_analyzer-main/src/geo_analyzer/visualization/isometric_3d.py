from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt


def build_isometric_view(buildings_gdf: gpd.GeoDataFrame, output_path: Path) -> Path | None:
    """
    Упрощённая MVP-визуализация массы застройки.

    Это не полноценная изометрия, но уже даёт читаемую картинку
    по footprint зданий.
    """
    if buildings_gdf is None or buildings_gdf.empty:
        return None

    gdf = buildings_gdf.copy()
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)

    fig, ax = plt.subplots(figsize=(10, 10))
    gdf.to_crs(epsg=3857).plot(ax=ax, linewidth=0.3, alpha=0.7)
    ax.set_title("Масса застройки вокруг точки")
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return output_path