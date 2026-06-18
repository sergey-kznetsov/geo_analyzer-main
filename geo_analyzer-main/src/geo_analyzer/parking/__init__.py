from __future__ import annotations

"""Parking package entrypoint."""

from typing import Any

import pandas as pd

from geo_analyzer.parking import fixes as _fixes
from geo_analyzer.parking.contract_v30 import PARKING_LOADER_VERSION, apply_2gis_contract_v30


def _safe_norm(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).replace("ё", "е").lower().strip()


_fixes._norm = _safe_norm
apply_2gis_contract_v30()

BUYABLE_SPACE_MARKERS = _fixes.BUYABLE_SPACE_MARKERS
CAR_OWNERSHIP_COEF = _fixes.CAR_OWNERSHIP_COEF
CLOSED_PARKING_MARKERS = _fixes.CLOSED_PARKING_MARKERS
DEFAULT_APARTMENTS_PER_BUILDING = _fixes.DEFAULT_APARTMENTS_PER_BUILDING
ParkingSupplyResult = _fixes.ParkingSupplyResult
calculate_parking_potential = _fixes.calculate_parking_potential
calculate_parking_supply = _fixes.calculate_parking_supply
classify_parking_potential = _fixes.classify_parking_potential

__all__ = ["BUYABLE_SPACE_MARKERS", "CAR_OWNERSHIP_COEF", "CLOSED_PARKING_MARKERS", "DEFAULT_APARTMENTS_PER_BUILDING", "PARKING_LOADER_VERSION", "ParkingSupplyResult", "calculate_parking_potential", "calculate_parking_supply", "classify_parking_potential"]
