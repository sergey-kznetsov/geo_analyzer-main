import geopandas as gpd
from shapely.geometry import Point

from geo_analyzer.enrichment.categories import classify_pois


def test_classify_shop_supermarket_legacy_osm_field():
    gdf = gpd.GeoDataFrame(
        [
            {
                "shop": "supermarket",
                "geometry": Point(37.6, 55.7),
            }
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )

    result = classify_pois(gdf)

    assert result.iloc[0]["Категория_2GIS"] == "Супермаркет"
    assert result.iloc[0]["functional_category"] == "Повседневная торговля и услуги"
    assert result.iloc[0]["criticality_score"] == 8
    assert result.iloc[0]["classification_status"] == "mapped"