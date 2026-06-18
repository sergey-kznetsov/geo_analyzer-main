from __future__ import annotations

from datetime import datetime

from geo_analyzer.core.models import AnalysisContext, ResolvedLocation
from geo_analyzer.core.settings import get_settings
from geo_analyzer.core.utils import ensure_directory, slugify


def build_analysis_context(location: ResolvedLocation) -> AnalysisContext:
    """Создаёт рабочий контекст анализа и выходные директории.

    Args:
        location: Нормализованная точка анализа.

    Returns:
        Контекст с путями для отчёта, изображений и сырых слоёв.
    """
    settings = get_settings()

    label = location.resolved_address or location.source_label or f"{location.latitude}_{location.longitude}"
    safe_label = slugify(label)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    result_dir = ensure_directory(settings.output_dir / f"{safe_label}_{timestamp}")
    images_dir = ensure_directory(result_dir / "images")
    raw_dir = ensure_directory(result_dir / "raw")

    return AnalysisContext(
        location=location,
        result_dir=result_dir,
        images_dir=images_dir,
        raw_dir=raw_dir,
        report_path=result_dir / "report.xlsx",
        summary_path=result_dir / "summary.txt",
        extra={
            "safe_label": safe_label,
            "timestamp": timestamp,
        },
    )