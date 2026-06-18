from __future__ import annotations

import re
from typing import Any

import geopandas as gpd
import pandas as pd


LIST_COLUMNS = {
    "source_categories_2gis",
    "rubrics_2gis",
    "category_groups_2gis",
}

TEXT_MERGE_COLUMNS = {
    "Категория_2GIS",
    "source_category_2gis",
    "rubric_id",
}

NUMERIC_MAX_COLUMNS = {
    "Рейтинг",
    "Количество_отзывов",
    "criticality_score",
}

STOP_KEYWORDS = {
    "остановка",
    "остановки",
    "остановочный пункт",
    "остановочный комплекс",
    "общественный транспорт",
    "автобусная остановка",
    "трамвайная остановка",
    "троллейбусная остановка",
    "bus_stop",
    "public_transport",
}

STOP_RUBRIC_IDS = {
    "450",
}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True

    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass

    return str(value).strip() == ""


def _normalize_text(value: Any) -> str:
    if _is_empty(value):
        return ""

    text = str(value).strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_coord(value: Any, digits: int = 5) -> str:
    if _is_empty(value):
        return ""

    try:
        return str(round(float(value), digits))
    except (TypeError, ValueError):
        return _normalize_text(value)


def _as_list(value: Any) -> list[Any]:
    if _is_empty(value):
        return []

    if isinstance(value, (list, tuple, set)):
        return [item for item in value if not _is_empty(item)]

    if isinstance(value, str) and "," in value:
        return [part.strip() for part in value.split(",") if part.strip()]

    return [value]


def _append_unique(values: list[Any], value: Any) -> None:
    if _is_empty(value):
        return

    if value not in values:
        values.append(value)


def _row_text(row: pd.Series) -> str:
    values: list[str] = []

    for column in [
        "Название",
        "name",
        "Адрес",
        "address",
        "Категория",
        "category",
        "Категория_2GIS",
        "source_category_2gis",
        "source_categories_2gis",
        "rubrics_2gis",
        "category_groups_2gis",
        "rubric_id",
        "branch_type_2gis",
        "type_id_2gis",
        "public_transport",
        "amenity",
        "highway",
    ]:
        if column not in row.index:
            continue

        raw_value = row.get(column)

        for item in _as_list(raw_value):
            values.append(_normalize_text(item))

    return " ".join(value for value in values if value)


def _is_transport_stop(row: pd.Series) -> bool:
    text = _row_text(row)

    if any(keyword in text for keyword in STOP_KEYWORDS):
        return True

    rubric_id = _normalize_text(row.get("rubric_id"))
    if rubric_id in STOP_RUBRIC_IDS:
        return True

    for value in _as_list(row.get("category_groups_2gis")):
        if _normalize_text(value) in STOP_RUBRIC_IDS:
            return True

    return False


def _normalize_stop_name(value: Any) -> str:
    text = _normalize_text(value)

    if not text:
        return ""

    text = re.sub(r"\bостановка\b", "", text)
    text = re.sub(r"\bостановки\b", "", text)
    text = re.sub(r"\bостановочный пункт\b", "", text)
    text = re.sub(r"\bостановочный комплекс\b", "", text)
    text = re.sub(r"\bобщественного транспорта\b", "", text)
    text = re.sub(r"\bобщественный транспорт\b", "", text)
    text = re.sub(r"[«»\"']", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")

    return text


def _stop_poi_key(row: pd.Series) -> str:
    """Ключ для дедубликации остановочных комплексов.

    Для обычных POI dgis_id нормален как главный ключ. Для остановок это
    опасно: одна физическая остановка может прийти разными записями,
    направлениями или рубриками. Поэтому остановки группируем по названию
    и координатам с округлением примерно до 10–15 метров.
    """
    name = _normalize_stop_name(row.get("Название") or row.get("name"))
    address = _normalize_text(row.get("Адрес") or row.get("address"))
    lat = _normalize_coord(row.get("Широта"), digits=4)
    lon = _normalize_coord(row.get("Долгота"), digits=4)

    if name and lat and lon:
        return f"stop_name_coords|{name}|{lat}|{lon}"

    if name and address:
        return f"stop_name_address|{name}|{address}"

    if lat and lon:
        return f"stop_coords|{lat}|{lon}"

    dgis_id = _normalize_text(row.get("dgis_id"))
    if dgis_id:
        return f"stop_dgis_id|{dgis_id}"

    fid = _normalize_text(row.get("fid"))
    if fid:
        return f"stop_fid|{fid}"

    return f"stop_unknown|{name}|{address}|{lat}|{lon}"


def _physical_poi_key(row: pd.Series) -> str:
    """Формирует ключ физического объекта.

    Для остановок используется отдельная логика, чтобы они тоже проходили
    дедубликацию как остановочные комплексы, а не считались разными POI
    только из-за разных id или направлений.
    """
    if _is_transport_stop(row):
        return _stop_poi_key(row)

    dgis_id = _normalize_text(row.get("dgis_id"))
    fid = _normalize_text(row.get("fid"))

    if dgis_id:
        return f"dgis_id|{dgis_id}"

    if fid:
        return f"fid|{fid}"

    name = _normalize_text(row.get("Название"))
    address = _normalize_text(row.get("Адрес"))

    if name and address:
        return f"name_address|{name}|{address}"

    lat = _normalize_coord(row.get("Широта"))
    lon = _normalize_coord(row.get("Долгота"))

    return f"name_coords|{name}|{lat}|{lon}"


def _merge_list_column(group: pd.DataFrame, column: str) -> list[Any]:
    result: list[Any] = []

    if column not in group.columns:
        return result

    for value in group[column].tolist():
        for item in _as_list(value):
            _append_unique(result, item)

    return result


def _merge_text_column(group: pd.DataFrame, column: str) -> str | pd.NA:
    values: list[str] = []

    if column not in group.columns:
        return pd.NA

    for value in group[column].tolist():
        for item in _as_list(value):
            text = str(item).strip()
            if text and text not in values:
                values.append(text)

    return ", ".join(values) if values else pd.NA


def _first_not_empty(group: pd.DataFrame, column: str) -> object:
    if column not in group.columns:
        return pd.NA

    for value in group[column].tolist():
        if not _is_empty(value):
            return value

    return pd.NA


def _max_numeric(group: pd.DataFrame, column: str) -> object:
    if column not in group.columns:
        return pd.NA

    numeric = pd.to_numeric(group[column], errors="coerce").dropna()
    if numeric.empty:
        return pd.NA

    return numeric.max()


def _merge_geometry(group: pd.DataFrame) -> object:
    if "geometry" not in group.columns:
        return pd.NA

    geometries = [value for value in group["geometry"].tolist() if not _is_empty(value)]

    if not geometries:
        return pd.NA

    return geometries[0]


def _merge_group(group: pd.DataFrame) -> pd.Series:
    base = group.iloc[0].copy()

    for column in group.columns:
        if column.startswith("_"):
            continue

        if column in LIST_COLUMNS:
            base[column] = _merge_list_column(group, column)
        elif column in TEXT_MERGE_COLUMNS:
            base[column] = _merge_text_column(group, column)
        elif column in NUMERIC_MAX_COLUMNS:
            base[column] = _max_numeric(group, column)
        elif column == "geometry":
            base[column] = _merge_geometry(group)
        else:
            base[column] = _first_not_empty(group, column)

    return base.drop(labels=["_physical_poi_key"], errors="ignore")


def merge_raw_pois(df: pd.DataFrame) -> pd.DataFrame:
    """Объединяет дубли POI до классификации.

    2GIS может вернуть один физический объект из нескольких rubric_id.
    Обычные POI дедублицируются по id / fid / названию / адресу.
    Остановки общественного транспорта дедублицируются отдельным ключом,
    потому что один остановочный комплекс может приходить несколькими
    записями, направлениями или разными id.
    """
    if df is None or df.empty:
        return df

    data = df.copy()

    for column in [
        "dgis_id",
        "fid",
        "Название",
        "name",
        "Адрес",
        "address",
        "Широта",
        "Долгота",
        "Категория_2GIS",
        "source_category_2gis",
        "source_categories_2gis",
        "rubrics_2gis",
        "category_groups_2gis",
        "rubric_id",
    ]:
        if column not in data.columns:
            data[column] = pd.NA

    data["_physical_poi_key"] = data.apply(_physical_poi_key, axis=1)

    rows: list[pd.Series] = []

    for _, group in data.groupby("_physical_poi_key", dropna=False):
        rows.append(_merge_group(group))

    result = pd.DataFrame(rows).reset_index(drop=True)

    if isinstance(df, gpd.GeoDataFrame):
        return gpd.GeoDataFrame(
            result,
            geometry="geometry",
            crs=df.crs or "EPSG:4326",
        )

    return result