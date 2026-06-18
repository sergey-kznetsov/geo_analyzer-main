from __future__ import annotations

import os

from geo_analyzer.ingestion.dgis.places_catalog_loader import load_places_near_point as _catalog_places_loader
from geo_analyzer.pipeline import analysis_runtime_compact as _runtime

os.environ.setdefault("GEO_ANALYZER_DGIS_PREFLIGHT_SCOPE", "all")
os.environ.setdefault("GEO_ANALYZER_DGIS_PLACE_RUBRIC_SCOPE", "all")

_runtime.load_places_near_point = _catalog_places_loader

resolve_location = _runtime.resolve_location
run_analysis = _runtime.run_analysis

__all__ = ["resolve_location", "run_analysis"]
