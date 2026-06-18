from __future__ import annotations

import os
from typing import Any

from geo_analyzer.ingestion.dgis.region_runtime_patch import ENV_REGION_ID
from geo_analyzer.parking import (
    CAR_OWNERSHIP_COEF,
    ParkingSupplyResult,
    calculate_parking_potential,
    calculate_parking_supply as _calculate_parking_supply,
    classify_parking_potential,
)

CAR_OWNERSHIP_RATE = CAR_OWNERSHIP_COEF

PARKING_TYPE_WEIGHTS = {
    "Бесплатная": 1.15,
    "Платная": 0.60,
    "Неизвестно": 0.75,
}

PARKING_ZONE_WEIGHTS = {
    "0–5 минут": 1.00,
    "5–10 минут": 0.70,
    "Итого до 10 минут": 1.00,
}


def calculate_parking_supply(*args: Any, **kwargs: Any) -> ParkingSupplyResult:
    """Совместимый вход для основного и автономного pipeline."""
    region_id = str(kwargs.pop("region_id", "") or "").strip()
    previous_region_id = os.getenv(ENV_REGION_ID)
    if region_id:
        os.environ[ENV_REGION_ID] = region_id
    try:
        return _calculate_parking_supply(*args, **kwargs)
    finally:
        if region_id:
            if previous_region_id is None:
                os.environ.pop(ENV_REGION_ID, None)
            else:
                os.environ[ENV_REGION_ID] = previous_region_id


def build_parking_summary_text(*args: Any, **kwargs: Any) -> str:
    """Прокси для обратной совместимости старых импортов."""
    try:
        from geo_analyzer.parking.supply import build_parking_summary_text as _build_parking_summary_text
    except ImportError:
        return ""
    return _build_parking_summary_text(*args, **kwargs)


__all__ = [
    "CAR_OWNERSHIP_RATE",
    "PARKING_TYPE_WEIGHTS",
    "PARKING_ZONE_WEIGHTS",
    "ParkingSupplyResult",
    "build_parking_summary_text",
    "calculate_parking_potential",
    "calculate_parking_supply",
    "classify_parking_potential",
]
