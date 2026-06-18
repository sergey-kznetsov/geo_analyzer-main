from __future__ import annotations

"""Regional dictionaries for observed 2GIS object cards.

This module extracts human-readable metadata from raw 2GIS objects: rubric
names, object types, attribute groups, attribute names/tags and examples. The
result is written only into debug profiles and is not exported to Excel.
"""

import json
from collections import Counter
from typing import Any, Iterable

MAX_SCAN_ITEMS = 32
MAX_EXAMPLE_LEN = 180


def _missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)
    except Exception:
        return False


def _text(value: Any) -> str:
    if _missing(value):
        return ""
    return str(value).strip()


def _kind(value: Any) -> str:
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


def _example(value: Any) -> str:
    try:
        if isinstance(value, (dict, list, tuple, set)):
            result = json.dumps(value, ensure_ascii=False, default=str)
        else:
            result = str(value)
    except Exception:
        result = repr(value)
    result = result.replace("\n", " ").replace("\r", " ").strip()
    return result[:MAX_EXAMPLE_LEN] + "…" if len(result) > MAX_EXAMPLE_LEN else result


def _iter_dicts(value: Any, depth: int = 0) -> Iterable[dict[str, Any]]:
    if depth > 10:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:MAX_SCAN_ITEMS]:
            yield from _iter_dicts(child, depth + 1)


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common() if name]


def _rubrics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for item in items:
        raw = item.get("rubrics")
        if not isinstance(raw, list):
            continue
        for rubric in raw:
            if not isinstance(rubric, dict):
                continue
            rubric_id = _text(rubric.get("id") or rubric.get("rubric_id") or rubric.get("code"))
            name = _text(rubric.get("name") or rubric.get("title") or rubric.get("caption") or rubric.get("display_name"))
            key = rubric_id or name
            if not key:
                continue
            row = catalog.setdefault(
                key,
                {
                    "rubric_id": rubric_id,
                    "name": name,
                    "parent_id": _text(rubric.get("parent_id")),
                    "type": _text(rubric.get("type")),
                    "count": 0,
                    "examples": [],
                },
            )
            row["count"] += 1
            obj_name = _text(item.get("name"))
            if obj_name and obj_name not in row["examples"] and len(row["examples"]) < 5:
                row["examples"].append(obj_name)
    return sorted(catalog.values(), key=lambda row: (-int(row.get("count", 0)), str(row.get("name") or row.get("rubric_id"))))


def _attribute_key(attribute: dict[str, Any]) -> str:
    for key in ("tag", "id", "code", "name", "caption", "title"):
        value = _text(attribute.get(key))
        if value:
            return value
    return ""


def _attribute_name(attribute: dict[str, Any]) -> str:
    for key in ("name", "caption", "title", "label", "tag", "id"):
        value = _text(attribute.get(key))
        if value:
            return value
    return ""


def _attribute_value(attribute: dict[str, Any]) -> Any:
    for key in ("value", "values", "text", "caption", "name"):
        if key in attribute and not isinstance(attribute.get(key), (dict, list)):
            return attribute.get(key)
    return None


def _attributes(items: list[dict[str, Any]]) -> dict[str, Any]:
    groups: Counter[str] = Counter()
    catalog: dict[str, dict[str, Any]] = {}
    for item in items:
        for node in _iter_dicts(item):
            raw = node.get("attributes")
            if not isinstance(raw, list):
                continue
            group = _text(node.get("name") or node.get("caption") or node.get("title") or node.get("tag") or node.get("id") or "Без группы")
            groups[group] += 1
            for attribute in raw:
                if not isinstance(attribute, dict):
                    continue
                key = _attribute_key(attribute)
                if not key:
                    continue
                row = catalog.setdefault(
                    key,
                    {
                        "key": key,
                        "names": Counter(),
                        "groups": Counter(),
                        "value_types": Counter(),
                        "examples": [],
                        "count": 0,
                    },
                )
                row["count"] += 1
                name = _attribute_name(attribute)
                if name:
                    row["names"][name] += 1
                if group:
                    row["groups"][group] += 1
                value = _attribute_value(attribute)
                row["value_types"][_kind(value)] += 1
                example = _example(value)
                if example and example not in row["examples"] and len(row["examples"]) < 8:
                    row["examples"].append(example)

    attributes: list[dict[str, Any]] = []
    for row in catalog.values():
        names = row["names"].most_common(5)
        groups_top = row["groups"].most_common(5)
        attributes.append(
            {
                "key": row["key"],
                "name": names[0][0] if names else row["key"],
                "all_names": [name for name, _ in names],
                "groups": [group for group, _ in groups_top],
                "value_types": dict(row["value_types"]),
                "examples": row["examples"],
                "count": row["count"],
            }
        )
    return {"groups": _counter_rows(groups), "attributes": sorted(attributes, key=lambda row: (-int(row.get("count", 0)), str(row.get("name"))))}


def _top_level(items: list[dict[str, Any]]) -> dict[str, Any]:
    keys: Counter[str] = Counter()
    object_types: Counter[str] = Counter()
    object_subtypes: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for item in items:
        for key, value in item.items():
            key_text = str(key)
            keys[key_text] += 1
            if key_text not in examples and isinstance(value, (str, int, float, bool)):
                examples[key_text] = _example(value)
        obj_type = _text(item.get("type"))
        obj_subtype = _text(item.get("subtype"))
        if obj_type:
            object_types[obj_type] += 1
        if obj_subtype:
            object_subtypes[obj_subtype] += 1
    return {
        "top_level_fields": [{"name": name, "count": count, "example": examples.get(name, "")} for name, count in keys.most_common()],
        "object_types": _counter_rows(object_types),
        "object_subtypes": _counter_rows(object_subtypes),
    }


def build_object_dictionary(items: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [item for item in items if isinstance(item, dict)]
    attributes = _attributes(clean)
    top_level = _top_level(clean)
    return {
        "items_count": len(clean),
        "rubrics": _rubrics(clean),
        "attribute_groups": attributes["groups"],
        "attributes": attributes["attributes"],
        "top_level_fields": top_level["top_level_fields"],
        "object_types": top_level["object_types"],
        "object_subtypes": top_level["object_subtypes"],
    }


def dictionary_keys(dictionary: dict[str, Any], section: str, key_name: str = "key") -> set[str]:
    values = dictionary.get(section, []) if isinstance(dictionary, dict) else []
    result: set[str] = set()
    if not isinstance(values, list):
        return result
    for item in values:
        if not isinstance(item, dict):
            continue
        value = _text(item.get(key_name) or item.get("rubric_id") or item.get("name"))
        if value:
            result.add(value)
    return result


__all__ = ["build_object_dictionary", "dictionary_keys"]
