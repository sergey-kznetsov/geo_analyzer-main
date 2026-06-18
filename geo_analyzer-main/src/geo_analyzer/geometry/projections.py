from __future__ import annotations

import geopandas as gpd


def ensure_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf.set_crs(epsg=4326)
    if str(gdf.crs).upper() != "EPSG:4326":
        return gdf.to_crs(epsg=4326)
    return gdf
