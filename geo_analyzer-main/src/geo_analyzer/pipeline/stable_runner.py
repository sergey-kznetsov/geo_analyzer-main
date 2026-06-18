from __future__ import annotations

from typing import Any, Callable

from geo_analyzer.core.models import LocationInput
from geo_analyzer.pipeline.analysis_pipeline import run_analysis as _run_analysis


ProgressCallback = Callable[..., None] | None


def run_analysis(
    location_input: LocationInput,
    *,
    progress_callback: ProgressCallback = None,
) -> dict[str, Any]:
    """Совместимый wrapper для GUI.

    GUI может передавать progress_callback. Старые CLI-запуски могут вызывать
    run_analysis только с LocationInput. Оба сценария должны работать.
    """
    return _run_analysis(
        location_input,
        progress_callback=progress_callback,
    )