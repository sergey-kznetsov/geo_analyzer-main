from __future__ import annotations

from pathlib import Path


def export_raw_layers(analysis_result: dict, raw_dir: Path) -> None:
    """Сохраняет сырые геослои в GeoJSON, если они не пустые."""
    for name in ["pois", "isochrones", "anti_drivers"]:
        layer = analysis_result.get(name)
        if layer is None or getattr(layer, "empty", True):
            continue
        target = raw_dir / f"{name}.geojson"
        layer.to_file(target, driver="GeoJSON")
