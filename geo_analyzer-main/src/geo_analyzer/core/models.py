from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class LocationInput:
    """Пользовательский ввод точки анализа."""

    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(slots=True)
class ResolvedLocation:
    """Нормализованная точка после геокодирования."""

    latitude: float
    longitude: float
    source_label: str
    resolved_address: str | None = None
    region_id: str | None = None
    region_name: str | None = None


@dataclass(slots=True)
class AnalysisContext:
    """Контекст запуска анализа и пути результата."""

    location: ResolvedLocation
    result_dir: Path
    images_dir: Path
    raw_dir: Path
    report_path: Path
    summary_path: Path
    extra: dict[str, Any] = field(default_factory=dict)
