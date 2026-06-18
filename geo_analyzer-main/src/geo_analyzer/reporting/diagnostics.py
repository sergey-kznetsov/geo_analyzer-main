from __future__ import annotations

import json
import math
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from shapely.geometry.base import BaseGeometry
except Exception:  # pragma: no cover
    BaseGeometry = ()  # type: ignore[assignment]


TECHNICAL_COLUMNS = {
    "raw_2gis",
    "geometry",
    "geometry_wkt",
    "geometry_hull",
    "point",
    "fid",
    "dgis_id",
    "id",
    "external_id",
}


def write_api_build_report(
    *,
    context: Any | None,
    location: Any | None,
    stage: str,
    payload: dict[str, Any] | None = None,
    error: BaseException | None = None,
    cache_root: Path | None = None,
) -> Path | None:
    """Пишет диагностический JSON по сборке данных API.

    Файл нужен для отладки: он сохраняет технические поля, геометрию,
    raw_2gis, размеры датафреймов, примеры строк и текст ошибки.
    Excel этим не загрязняется.
    """
    payload = payload or {}

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "status": "error" if error else "ok",
        "location": _location_payload(location),
        "context": _context_payload(context),
        "error": _error_payload(error),
        "data": {key: _serialize_value(value) for key, value in payload.items()},
    }

    output_paths = _resolve_output_paths(context, cache_root=cache_root)
    last_path: Path | None = None

    for path in output_paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            last_path = path
        except Exception:
            continue

    return last_path


def _resolve_output_paths(context: Any | None, *, cache_root: Path | None = None) -> list[Path]:
    paths: list[Path] = []

    if context is not None:
        raw_dir = getattr(context, "raw_dir", None)
        result_dir = getattr(context, "result_dir", None)
        extra = getattr(context, "extra", {}) or {}

        if raw_dir:
            paths.append(Path(raw_dir) / "api_build_report.json")

        safe_label = str(extra.get("safe_label") or "").strip()
        if safe_label:
            root = cache_root
            if root is None:
                if result_dir:
                    # result_dir обычно: output/<address_timestamp>.
                    # cache кладём рядом с output, чтобы файл не потерялся при ошибке экспорта.
                    root = Path(result_dir).parent.parent / "data" / "cache" / "api_build_reports"
                else:
                    root = Path("data") / "cache" / "api_build_reports"
            paths.append(Path(root) / f"{safe_label}.json")

    if not paths:
        paths.append(Path("data") / "cache" / "api_build_reports" / "latest.json")

    return paths


def _location_payload(location: Any | None) -> dict[str, Any] | None:
    if location is None:
        return None

    return {
        "resolved_address": getattr(location, "resolved_address", None),
        "source_label": getattr(location, "source_label", None),
        "latitude": getattr(location, "latitude", None),
        "longitude": getattr(location, "longitude", None),
    }


def _context_payload(context: Any | None) -> dict[str, Any] | None:
    if context is None:
        return None

    extra = getattr(context, "extra", {}) or {}
    return {
        "result_dir": str(getattr(context, "result_dir", "")),
        "raw_dir": str(getattr(context, "raw_dir", "")),
        "report_path": str(getattr(context, "report_path", "")),
        "summary_path": str(getattr(context, "summary_path", "")),
        "safe_label": extra.get("safe_label"),
        "timestamp": extra.get("timestamp"),
    }


def _error_payload(error: BaseException | None) -> dict[str, Any] | None:
    if error is None:
        return None

    return {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": traceback.format_exception(type(error), error, error.__traceback__),
    }


def _serialize_value(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return _dataframe_payload(value)

    if isinstance(value, pd.Series):
        return _series_payload(value)

    if isinstance(value, dict):
        return {str(key): _serialize_value(val) for key, val in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in list(value)[:200]]

    if isinstance(value, Path):
        return str(value)

    if _is_geometry(value):
        return _geometry_payload(value)

    if _is_scalar(value):
        return _safe_scalar(value)

    return _truncate_string(str(value))


def _dataframe_payload(df: pd.DataFrame) -> dict[str, Any]:
    if df is None:
        return {"type": "DataFrame", "is_none": True}

    safe_df = df.copy()
    rows_total = int(len(safe_df))
    columns = [str(col) for col in safe_df.columns]

    technical_columns = [col for col in columns if col in TECHNICAL_COLUMNS or col.lower() in TECHNICAL_COLUMNS]
    geometry_columns = _detect_geometry_columns(safe_df)

    sample = []
    if rows_total:
        sample_df = safe_df.head(50)
        for _, row in sample_df.iterrows():
            sample.append({str(col): _serialize_cell(row.get(col)) for col in sample_df.columns})

    return {
        "type": "DataFrame",
        "rows": rows_total,
        "columns_count": len(columns),
        "columns": columns,
        "technical_columns": technical_columns,
        "geometry_columns": geometry_columns,
        "dtypes": {str(col): str(dtype) for col, dtype in safe_df.dtypes.items()},
        "nulls": {str(col): int(safe_df[col].isna().sum()) for col in safe_df.columns},
        "sample_limit": 50,
        "sample": sample,
    }


def _series_payload(series: pd.Series) -> dict[str, Any]:
    return {
        "type": "Series",
        "name": str(series.name),
        "size": int(series.size),
        "dtype": str(series.dtype),
        "sample": [_serialize_cell(value) for value in series.head(50).tolist()],
    }


def _serialize_cell(value: Any) -> Any:
    if _is_geometry(value):
        return _geometry_payload(value)

    if isinstance(value, dict):
        return {str(key): _serialize_cell(val) for key, val in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_serialize_cell(item) for item in list(value)[:100]]

    if isinstance(value, Path):
        return str(value)

    if _is_scalar(value):
        return _safe_scalar(value)

    return _truncate_string(str(value))


def _detect_geometry_columns(df: pd.DataFrame) -> list[str]:
    result: list[str] = []

    for column in df.columns:
        values = df[column].dropna().head(20).tolist()
        if not values:
            continue

        if any(_is_geometry(value) for value in values):
            result.append(str(column))
            continue

        if str(column).lower() in {"geometry", "geometry_wkt", "geometry_hull"}:
            result.append(str(column))

    return result


def _is_geometry(value: Any) -> bool:
    try:
        return isinstance(value, BaseGeometry)
    except Exception:
        return False


def _geometry_payload(geom: Any) -> dict[str, Any]:
    try:
        return {
            "type": getattr(geom, "geom_type", type(geom).__name__),
            "is_empty": bool(getattr(geom, "is_empty", False)),
            "bounds": list(getattr(geom, "bounds", [])),
            "wkt": _truncate_string(getattr(geom, "wkt", str(geom)), 2000),
        }
    except Exception:
        return {"type": type(geom).__name__, "value": _truncate_string(str(geom))}


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _safe_scalar(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, str):
        return _truncate_string(value)

    return value


def _truncate_string(value: str, limit: int = 5000) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"
