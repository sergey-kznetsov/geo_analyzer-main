from __future__ import annotations

import pandas as pd

from geo_analyzer.core.settings import get_settings


def load_city_reference(city_slug: str | None = None) -> pd.DataFrame:
    settings = get_settings()
    base_dir = settings.data_dir / "processed" / "city_reference"

    if city_slug:
        candidate = base_dir / f"{city_slug}.csv"
        if candidate.exists():
            return pd.read_csv(candidate)

    fallback = base_dir / "default.csv"
    if fallback.exists():
        return pd.read_csv(fallback)

    return pd.DataFrame(columns=["metric", "p10", "p25", "p50", "p75", "p90"])