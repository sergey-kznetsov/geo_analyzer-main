import pandas as pd

from geo_analyzer.metrics.environment_quality import build_quality_scores


def test_quality_scores_not_empty():
    poi_counts = pd.DataFrame([
        {"minutes": 10, "category": "Ритейл", "count": 3},
        {"minutes": 10, "category": "Еда и напитки", "count": 2},
    ])
    categories = pd.DataFrame([
        {"category": "Ритейл", "count": 3, "share_percent": 60},
        {"category": "Еда и напитки", "count": 2, "share_percent": 40},
    ])
    network = pd.DataFrame([{"metric": "intersection_density_km2", "value": 2.0}])
    result = build_quality_scores(poi_counts, categories, network)
    assert not result.empty
