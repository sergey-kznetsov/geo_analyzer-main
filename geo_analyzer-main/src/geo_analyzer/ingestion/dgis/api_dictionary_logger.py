from __future__ import annotations

"""Debug persistence for regional 2GIS object dictionaries."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.api_dictionary import build_object_dictionary, dictionary_keys

logger = get_logger("geo_analyzer.dgis.api_dictionary")
_SAFE_FILENAME_RE = re.compile(r"[^0-9a-zA-Zа-яА-Я_.-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(value: Any) -> str:
    text = str(value or "unknown").strip() or "unknown"
    text = _SAFE_FILENAME_RE.sub("_", text)
    return text.strip("._")[:120] or "unknown"


def _dir() -> Path:
    settings = get_settings()
    path = settings.logs_dir / "dgis_api_profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(region_id: str, source: str, object_kind: str) -> Path:
    return _dir() / f"region_{_safe_name(region_id)}__{_safe_name(source)}__{_safe_name(object_kind)}__dictionary.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def observe_object_dictionary(
    *,
    region_id: str | int | None,
    source: str,
    object_kind: str,
    items: list[dict[str, Any]],
    request_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean = [item for item in items if isinstance(item, dict)]
    if not clean:
        return {}
    try:
        region_text = str(region_id or "unknown")
        path = _path(region_text, source, object_kind)
        previous = _load(path)
        previous_dictionary = previous.get("object_dictionary") if isinstance(previous, dict) else {}
        dictionary = build_object_dictionary(clean)

        new_attributes = sorted(dictionary_keys(dictionary, "attributes") - dictionary_keys(previous_dictionary, "attributes"))
        missing_attributes = sorted(dictionary_keys(previous_dictionary, "attributes") - dictionary_keys(dictionary, "attributes"))
        new_rubrics = sorted(dictionary_keys(dictionary, "rubrics", "rubric_id") - dictionary_keys(previous_dictionary, "rubrics", "rubric_id"))
        missing_rubrics = sorted(dictionary_keys(previous_dictionary, "rubrics", "rubric_id") - dictionary_keys(dictionary, "rubrics", "rubric_id"))
        new_top_fields = sorted(dictionary_keys(dictionary, "top_level_fields", "name") - dictionary_keys(previous_dictionary, "top_level_fields", "name"))
        missing_top_fields = sorted(dictionary_keys(previous_dictionary, "top_level_fields", "name") - dictionary_keys(dictionary, "top_level_fields", "name"))

        payload = {
            "observed_at": _now(),
            "region_id": region_text,
            "source": source,
            "object_kind": object_kind,
            "request_params": request_params or {},
            "object_dictionary": dictionary,
            "new_attributes": new_attributes,
            "missing_attributes": missing_attributes,
            "new_rubrics": new_rubrics,
            "missing_rubrics": missing_rubrics,
            "new_top_level_fields": new_top_fields,
            "missing_top_level_fields": missing_top_fields,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        if new_attributes:
            logger.info("2GIS dictionary region_id=%s source=%s kind=%s: new attributes=%s", region_text, source, object_kind, ", ".join(new_attributes[:25]))
        if new_rubrics:
            logger.info("2GIS dictionary region_id=%s source=%s kind=%s: new rubrics=%s", region_text, source, object_kind, ", ".join(new_rubrics[:25]))
        return payload
    except Exception as exc:
        logger.warning("Не удалось сохранить словарь полей 2GIS API: %s", exc)
        return {}


__all__ = ["observe_object_dictionary"]
