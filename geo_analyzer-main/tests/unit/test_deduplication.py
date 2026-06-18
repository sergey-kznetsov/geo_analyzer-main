import geopandas as gpd
from shapely.geometry import Point

from geo_analyzer.enrichment.deduplication import spatial_deduplicate_sources


def test_deduplication_prefers_yandex_and_removes_close_duplicate():
    osm = gpd.GeoDataFrame(
        [
            {
                "name": "Кофейня 1",
                "category": "Еда и напитки",
                "geometry": Point(37.62000, 55.75000),
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )

    yandex = gpd.GeoDataFrame(
        [
            {
                "name": "Кофейня 1",
                "category": "Еда и напитки",
                "geometry": Point(37.62010, 55.75010),
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )

    result = spatial_deduplicate_sources(osm, yandex, distance_threshold_m=50)

    assert len(result) == 1
    assert result.iloc[0]["name"] == "Кофейня 1"
    assert result.iloc[0]["source"] == "yandex"