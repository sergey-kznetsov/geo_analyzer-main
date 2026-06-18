from __future__ import annotations

import os
from typing import Any

from . import region_runtime_patch as _region_runtime_patch
from .geocoder import DGISGeocoder
from . import places_enriched_loader as _places_enriched_loader
from . import preflight as _preflight

_region_runtime_patch.apply_patch()

_ORIGINAL_LOAD_PLACES_NEAR_POINT = _places_enriched_loader.load_places_near_point
_ORIGINAL_RUN_DGIS_PREFLIGHT = _preflight.run_dgis_preflight


def _with_region_env(region_id: str | None):
    class _RegionEnv:
        def __enter__(self) -> None:
            self.previous = os.getenv(_region_runtime_patch.ENV_REGION_ID)
            region = str(region_id or "").strip()
            if region:
                os.environ[_region_runtime_patch.ENV_REGION_ID] = region

        def __exit__(self, *_exc: Any) -> None:
            region = str(region_id or "").strip()
            if not region:
                return
            if self.previous is None:
                os.environ.pop(_region_runtime_patch.ENV_REGION_ID, None)
            else:
                os.environ[_region_runtime_patch.ENV_REGION_ID] = self.previous

    return _RegionEnv()


def load_places_near_point(latitude: float, longitude: float, radius_m: int, *, region_id: str | None = None):
    with _with_region_env(region_id):
        return _ORIGINAL_LOAD_PLACES_NEAR_POINT(latitude, longitude, radius_m)


def run_dgis_preflight(latitude: float, longitude: float, radius_m: int, *, region_id: str | None = None):
    with _with_region_env(region_id):
        return _ORIGINAL_RUN_DGIS_PREFLIGHT(latitude, longitude, radius_m)


_places_enriched_loader.load_places_near_point = load_places_near_point
_preflight.run_dgis_preflight = run_dgis_preflight

__all__ = ["DGISGeocoder", "load_places_near_point", "run_dgis_preflight"]
