from __future__ import annotations

"""Debug-only profiler for raw 2GIS API responses.

The profiler observes every available field returned by 2GIS, stores field-path
profiles by region/source/object kind, and compares the current response shape
with the previous saved snapshot. The data is written to ``logs/dgis_api_profiles``
and is intentionally not added to Excel reports.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings

logger = get_logger("geo_analyzer.dgis.api_profile")
PROFILE_VERSION = "dgis_api_profile_v1"
MAX_EXAMPLE_LEN = 240
MAX_LIST_ITEMS_TO_SCAN = 5
MAX_DEPTH = 10


_SECRET_KEYS = {"key", "api_key", "apikey", "token", "access_token", "authorization"}
_SAFE_FILENAME_RE = re.compile(r"[^0-9a-zA-Zа-яА-Я_.-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(value: Any) -> str:
    text = str(value or "unknown").strip() or "unknown"
    text = _SAFE_FILENAME_RE.sub("_", text)
    return text.strip("._")[:120] or "unknown"


def _profile_dir() -> Path:
    settings = get_settings()
    path = settings.logs_dir / "dgis_api_profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _profile_path(region_id: str, source: str, object_kind: str) -> Path:
    return _profile_dir() / f"region_{_safe_name(region_id)}__{_safe_name(source)}__{_safe_name(object_kind)}.json"


def _snapshot_path(region_id: str, source: str, object_kind: str) -> Path:
    return _profile_dir() / f"region_{_safe_name(region_id)}__{_safe_name(source)}__{_safe_name(object_kind)}__latest_sample.json"


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    return type(value).__name__


def _safe_example(value: Any) -> str:
    try:
        if isinstance(value, (dict, list, tuple, set)):
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value)
    except Exception:
        text = repr(value)
    text = text.replace("\n", " ").replace("\r", " ").strip()
    if len(text) > MAX_EXAMPLE_LEN:
        return text[:MAX_EXAMPLE_LEN] + "…"
    return text


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if str(key).lower() in _SECRET_KEYS:
                result[key] = "***"
            else:
                result[key] = _redact(child)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _iter_paths(value: Any, prefix: str = "", depth: int = 0):
    if depth > MAX_DEPTH:
        return
    kind = _type_name(value)
    if prefix:
        yield prefix, kind, value
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            yield from _iter_paths(child, path, depth + 1)
    elif isinstance(value, list):
        if prefix:
            yield f"{prefix}[]", "list_item", None
        for child in value[:MAX_LIST_ITEMS_TO_SCAN]:
            path = f"{prefix}[]" if prefix else "[]"
            yield from _iter_paths(child, path, depth + 1)


def _extract_items(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return [item for item in result["items"] if isinstance(item, dict)]
    if isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def build_field_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact field-path profile from raw 2GIS items."""
    path_counts: Counter[str] = Counter()
    type_counts: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, str] = {}

    for item in items:
        for path, kind, value in _iter_paths(item):
            path_counts[path] += 1
            type_counts[path][kind] += 1
            if path not in examples and value is not None and kind not in {"dict", "list", "list_item"}:
                examples[path] = _safe_example(value)

    fields: list[dict[str, Any]] = []
    for path in sorted(path_counts):
        fields.append(
            {
                "path": path,
                "count": path_counts[path],
                "types": dict(sorted(type_counts[path].items())),
                "example": examples.get(path, ""),
            }
        )

    return {
        "profile_version": PROFILE_VERSION,
        "items_count": len(items),
        "field_count": len(fields),
        "fields": fields,
    }


def _load_previous(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _paths_from_profile(profile: dict[str, Any]) -> set[str]:
    fields = profile.get("fields")
    if not isinstance(fields, list):
        return set()
    return {str(item.get("path")) for item in fields if isinstance(item, dict) and item.get("path")}


def observe_items(
    *,
    region_id: str | int | None,
    source: str,
    object_kind: str,
    items: list[dict[str, Any]],
    request_params: dict[str, Any] | None = None,
    raw_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store and compare a field profile for raw 2GIS items.

    Returns the saved profile with ``new_paths`` and ``missing_paths`` compared
    to the previous snapshot. Errors are logged and swallowed so profiling never
    breaks the main analysis pipeline.
    """
    clean_items = [item for item in items if isinstance(item, dict)]
    if not clean_items:
        return {}

    try:
        region_text = str(region_id or "unknown")
        path = _profile_path(region_text, source, object_kind)
        previous = _load_previous(path)
        previous_paths = _paths_from_profile(previous)

        profile = build_field_profile(clean_items)
        current_paths = _paths_from_profile(profile)
        new_paths = sorted(current_paths - previous_paths)
        missing_paths = sorted(previous_paths - current_paths)

        profile.update(
            {
                "observed_at": _utc_now(),
                "region_id": region_text,
                "source": source,
                "object_kind": object_kind,
                "request_params": _redact(request_params or {}),
                "new_paths": new_paths,
                "missing_paths": missing_paths,
            }
        )

        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        sample = {
            "observed_at": profile["observed_at"],
            "region_id": region_text,
            "source": source,
            "object_kind": object_kind,
            "request_params": _redact(request_params or {}),
            "items_sample": _redact(clean_items[:20]),
        }
        if isinstance(raw_response, dict):
            sample["raw_meta"] = _redact(raw_response.get("meta", {}))
            sample["raw_total"] = (raw_response.get("result") or {}).get("total") if isinstance(raw_response.get("result"), dict) else None
        _snapshot_path(region_text, source, object_kind).write_text(
            json.dumps(sample, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        if new_paths:
            logger.info(
                "2GIS API profile region_id=%s source=%s kind=%s: new fields=%s",
                region_text,
                source,
                object_kind,
                ", ".join(new_paths[:25]),
            )
        if missing_paths:
            logger.info(
                "2GIS API profile region_id=%s source=%s kind=%s: missing fields=%s",
                region_text,
                source,
                object_kind,
                ", ".join(missing_paths[:25]),
            )
        return profile
    except Exception as exc:
        logger.warning("Не удалось сохранить профиль полей 2GIS API: %s", exc)
        return {}


def observe_response(
    *,
    region_id: str | int | None,
    source: str,
    object_kind: str,
    data: dict[str, Any] | None,
    request_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract items from a raw 2GIS response and profile them."""
    items = _extract_items(data)
    return observe_items(
        region_id=region_id,
        source=source,
        object_kind=object_kind,
        items=items,
        request_params=request_params,
        raw_response=data if isinstance(data, dict) else None,
    )


__all__ = ["build_field_profile", "observe_items", "observe_response"]
