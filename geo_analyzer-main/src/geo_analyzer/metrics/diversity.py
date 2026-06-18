from __future__ import annotations

import math
import pandas as pd


def shannon_entropy(category_summary: pd.DataFrame) -> float:
    if category_summary.empty:
        return 0.0
    total = float(category_summary["count"].sum())
    entropy = 0.0
    for count in category_summary["count"]:
        p = float(count) / total
        entropy -= p * math.log(p) if p > 0 else 0.0
    return round(entropy, 4)
