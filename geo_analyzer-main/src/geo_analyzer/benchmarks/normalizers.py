from __future__ import annotations

import pandas as pd


def normalize_score_to_city_context(score: float, reference_row: pd.Series | None = None) -> float:
    if reference_row is None or reference_row.empty:
        return float(score)

    p10 = float(reference_row.get("p10", 0))
    p90 = float(reference_row.get("p90", 100))

    if p90 <= p10:
        return float(score)

    normalized = (float(score) - p10) / (p90 - p10) * 100
    return max(0.0, min(100.0, round(normalized, 2)))