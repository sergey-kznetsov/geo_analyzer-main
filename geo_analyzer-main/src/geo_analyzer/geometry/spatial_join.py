from __future__ import annotations

import math
import re
from typing import Any

import geopandas as gpd
import pandas as pd


def _prepare_points_layer(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Приводит входной слой POI к точкам в EPSG:4326."""
    prepared = gdf.copy()

    if prepared.crs is None:
        prepared = prepared.set_crs(epsg=4326)
    elif str(prepared.crs).upper() != "EPSG:4326":
        prepared = prepared.to_crs(epsg=4326)

    prepared["geometry"] = prepared.geometry.apply(
        lambda geom: geom.representative_point()
        if geom is not None and geom.geom_type != "Point"
        else geom
    )

    prepared = prepared[prepared.geometry.notna()].copy()
    prepared = prepared[~prepared.geometry.is_empty].copy()

    return prepared


def _prepare_isochrone_layer(isochrones: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Готовит слой изохрон для spatial join.

    Ожидается, что build_isochrones возвращает непересекающиеся кольца.
    Для обратной совместимости недостающие поля создаются из ``minutes``.
    """
    polygons = isochrones.copy()

    if polygons.crs is None:
        polygons = polygons.set_crs(epsg=4326)
    elif str(polygons.crs).upper() != "EPSG:4326":
        polygons = polygons.to_crs(epsg=4326)

    if "minutes" not in polygons.columns:
        raise ValueError("В слое изохрон отсутствует колонка minutes.")

    polygons = polygons.sort_values("minutes").reset_index(drop=True)

    if "from_minutes" not in polygons.columns:
        previous = 0
        values: list[int] = []

        for value in polygons["minutes"].astype(int).tolist():
            values.append(previous)
            previous = value

        polygons["from_minutes"] = values

    if "to_minutes" not in polygons.columns:
        polygons["to_minutes"] = polygons["minutes"].astype(int)

    if "range_label" not in polygons.columns:
        polygons["range_label"] = polygons.apply(
            lambda row: f"{int(row['from_minutes'])}-{int(row['to_minutes'])}",
            axis=1,
        )

    if "range_label_ru" not in polygons.columns:
        polygons["range_label_ru"] = polygons.apply(
            lambda row: f"{int(row['from_minutes'])}–{int(row['to_minutes'])} мин",
            axis=1,
        )

    return polygons


def _normalize_key(value: Any) -> str:
    """Нормализует текст для ключей дедупликации."""
    if value is None:
        return ""

    if isinstance(value, float) and pd.isna(value):
        return ""

    text = str(value).lower().replace("ё", "е").strip()
    text = re.sub(r"\s+", " ", text)

    return text


def _as_list(value: Any) -> list[str]:
    """Преобразует значение в список строк без ошибки на list/NaN."""
    if value is None:
        return []

    if isinstance(value, float) and pd.isna(value):
        return []

    if isinstance(value, (list, tuple, set)):
        result: list[str] = []

        for item in value:
            if item is None:
                continue

            if isinstance(item, float) and pd.isna(item):
                continue

            text = str(item).strip()

            if text:
                result.append(text)

        return result

    text = str(value).strip()

    if not text:
        return []

    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]

    return [text]


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Считает расстояние между двумя координатами в метрах."""
    earth_radius_m = 6_371_000.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )

    return earth_radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_joined(empty_columns: list[str], crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns=empty_columns, geometry="geometry", crs=crs)


def _fallback_attach_by_distance(
    points: gpd.GeoDataFrame,
    polygons: gpd.GeoDataFrame,
    empty_columns: list[str],
) -> gpd.GeoDataFrame:
    """????????? ???????? POI ? ????? 0?5 / 5?10 / 10?15 ?? ??????????.

    ???????????? ?????? ???? ??????? spatial join ?? ????????? 2GIS ?????? ?????.
    ??? ???????? ????? ?? ????????, ????? POI ?????????, ???????? ?????????,
    ?? ??-?? ?????????/CRS/?????????? ????????? ??? ??????? ?????? ?? ??????.
    """
    if points is None or points.empty or polygons is None or polygons.empty:
        return _empty_joined(empty_columns, crs=str(getattr(points, "crs", None) or "EPSG:4326"))

    center_lat = None
    center_lon = None
    speed_kph = None

    for _, zone in polygons.iterrows():
        center_lat = _safe_float(zone.get("center_latitude"))
        center_lon = _safe_float(zone.get("center_longitude"))
        speed_kph = _safe_float(zone.get("walk_speed_kph"))
        if center_lat is not None and center_lon is not None:
            break

    if center_lat is None or center_lon is None:
        try:
            union = polygons.to_crs(epsg=4326).geometry.union_all()
        except Exception:
            union = polygons.to_crs(epsg=4326).unary_union

        centroid = union.centroid
        center_lat = float(centroid.y)
        center_lon = float(centroid.x)

    if speed_kph is None or speed_kph <= 0:
        speed_kph = 4.8

    meters_per_minute = float(speed_kph) * 1000.0 / 60.0

    zones = polygons.sort_values("to_minutes" if "to_minutes" in polygons.columns else "minutes").copy()

    rows: list[dict[str, Any]] = []

    for _, poi in points.iterrows():
        geometry = poi.geometry

        if geometry is None or geometry.is_empty:
            continue

        lat = _safe_float(poi.get("??????"))
        lon = _safe_float(poi.get("???????"))

        if lat is None:
            lat = float(geometry.y)

        if lon is None:
            lon = float(geometry.x)

        distance_m = _distance_m(float(center_lat), float(center_lon), float(lat), float(lon))

        for _, zone in zones.iterrows():
            from_minutes = _safe_float(zone.get("from_minutes")) or 0.0
            to_minutes = _safe_float(zone.get("to_minutes"))
            if to_minutes is None:
                to_minutes = _safe_float(zone.get("minutes"))

            if to_minutes is None:
                continue

            lower_m = from_minutes * meters_per_minute
            upper_m = to_minutes * meters_per_minute

            if distance_m <= upper_m and (from_minutes <= 0 or distance_m > lower_m):
                row = poi.to_dict()
                row["?????_??????"] = int(round(to_minutes))
                row["travel_time_min"] = int(round(to_minutes))
                row["??_?????"] = int(round(from_minutes))
                row["??_?????"] = int(round(to_minutes))
                row["????????_???????????"] = f"{int(round(from_minutes))}-{int(round(to_minutes))}"
                row["????_???????????"] = f"{int(round(from_minutes))}?{int(round(to_minutes))} ???"
                row["accessibility_zone"] = row["????_???????????"]
                row["_fallback_distance_m"] = round(distance_m, 1)
                row["_isochrone_join_method"] = "distance_fallback"
                rows.append(row)
                break

    if not rows:
        return _empty_joined(empty_columns, crs=str(points.crs or "EPSG:4326"))

    joined = gpd.GeoDataFrame(rows, geometry="geometry", crs=points.crs or "EPSG:4326")
    joined["?????_??????"] = pd.to_numeric(joined["?????_??????"], errors="coerce").astype("Int64")
    joined = joined.dropna(subset=["?????_??????"]).sort_values(["?????_??????"]).reset_index(drop=True)

    if joined.empty:
        return _empty_joined(empty_columns, crs=str(points.crs or "EPSG:4326"))

    joined = _ensure_required_fields(joined)
    joined["_base_poi_key"] = joined.apply(_deduplication_key, axis=1)
    joined = (
        joined.sort_values(["_base_poi_key", "?????_??????"])
        .drop_duplicates(subset=["_base_poi_key"], keep="first")
        .drop(columns=["_base_poi_key"], errors="ignore")
        .reset_index(drop=True)
    )
    joined = _ensure_required_fields(joined)
    joined = deduplicate_transport_stops(joined)
    joined = deduplicate_same_poi(joined)
    joined = _ensure_required_fields(joined)

    return gpd.GeoDataFrame(joined, geometry="geometry", crs=points.crs or "EPSG:4326").reset_index(drop=True)


def _join_unique(values: pd.Series) -> str | pd.NA:
    """Склеивает уникальные непустые значения через запятую."""
    unique_values: list[str] = []

    for value in values.dropna().tolist():
        for item in _as_list(value):
            if item and item not in unique_values:
                unique_values.append(item)

    return ", ".join(unique_values) if unique_values else pd.NA


def _join_unique_list(values: pd.Series) -> list[str]:
    """Склеивает списки значений без дублей."""
    result: list[str] = []

    for value in values.dropna().tolist():
        for item in _as_list(value):
            normalized = _normalize_key(item)

            if normalized and normalized not in result:
                result.append(normalized)

    return result


def _first_not_empty(values: pd.Series) -> object:
    """Возвращает первое непустое значение из серии."""
    for value in values.tolist():
        if value is None:
            continue

        if isinstance(value, float) and pd.isna(value):
            continue

        if isinstance(value, (list, tuple, set)):
            if len(value) > 0:
                return value
            continue

        if str(value).strip():
            return value

    return pd.NA


def _max_numeric(values: pd.Series) -> object:
    """Возвращает максимальное числовое значение или NA."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()

    if numeric.empty:
        return pd.NA

    return numeric.max()


def _min_numeric(values: pd.Series) -> object:
    """Возвращает минимальное числовое значение или NA."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()

    if numeric.empty:
        return pd.NA

    return numeric.min()


def _deduplication_key(row: pd.Series) -> str:
    """Формирует ключ дедупликации POI внутри изохрон.

    Приоритет:
    1. dgis_id/fid;
    2. нормализованное название + адрес;
    3. название + координаты.

    Зона доступности не входит в ключ, чтобы один и тот же парк или объект
    не повторялся в нескольких изохронах. Ближайшая зона выбирается раньше
    при сортировке по ``Минут_пешком``.
    """
    dgis_id = _normalize_key(row.get("dgis_id"))
    fid = _normalize_key(row.get("fid"))

    if dgis_id:
        return f"id|{dgis_id}"

    if fid:
        return f"fid|{fid}"

    name = _normalize_key(row.get("Название"))
    address = _normalize_key(row.get("Адрес"))

    if name and address:
        return f"name_address|{name}|{address}"

    lat = _normalize_key(row.get("Широта"))
    lon = _normalize_key(row.get("Долгота"))

    return f"name_coords|{name}|{lat}|{lon}"


def _aggregation_map(df: pd.DataFrame) -> dict[str, object]:
    """Формирует правила агрегации для дедупликации."""
    aggregation: dict[str, object] = {}

    list_columns = {
        "source_categories_2gis",
        "rubrics_2gis",
        "category_groups_2gis",
    }

    joined_text_columns = {
        "Поисковый_запрос",
        "Тип_транспортного_объекта",
        "source_category_2gis",
    }

    min_columns = {
        "Минут_пешком",
        "travel_time_min",
        "От_минут",
        "До_минут",
    }

    max_columns = {
        "Рейтинг",
        "Количество_отзывов",
        "criticality_score",
    }

    for column in df.columns:
        if column.startswith("_"):
            continue

        if column in min_columns:
            aggregation[column] = _min_numeric
        elif column in max_columns:
            aggregation[column] = _max_numeric
        elif column in joined_text_columns:
            aggregation[column] = _join_unique
        elif column in list_columns:
            aggregation[column] = _join_unique_list
        elif column == "geometry":
            aggregation[column] = "first"
        else:
            aggregation[column] = _first_not_empty

    return aggregation


def _aggregate_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Объединяет дубли внутри уже привязанного слоя."""
    if df.empty:
        return df

    prepared = df.copy()
    prepared["_dedupe_key"] = prepared.apply(_deduplication_key, axis=1)

    aggregation = _aggregation_map(prepared)

    result = prepared.groupby("_dedupe_key", as_index=False).agg(aggregation)
    result = result.drop(columns=["_dedupe_key"], errors="ignore")

    return result.reset_index(drop=True)


def _deduplicate_same_name_nearby(df: pd.DataFrame, distance_threshold_m: float = 50.0) -> pd.DataFrame:
    """Объединяет одинаковые объекты с одинаковым названием в пределах 50 метров.

    Категория и зона не используются как обязательная часть ключа, потому что
    один и тот же парк может прийти из 2GIS разными категориями или попасть
    на границу изохроны.
    """
    if df.empty:
        return df

    if not {"Название", "Широта", "Долгота"}.issubset(df.columns):
        return df

    prepared = df.copy()
    prepared["_name_key"] = prepared["Название"].map(_normalize_key)

    used: set[int] = set()
    groups: list[list[int]] = []

    for idx, row in prepared.iterrows():
        if idx in used:
            continue

        group = [idx]
        used.add(idx)

        name = row["_name_key"]
        lat = row.get("Широта")
        lon = row.get("Долгота")

        if not name or pd.isna(lat) or pd.isna(lon):
            groups.append(group)
            continue

        candidates = prepared[
            (prepared.index != idx)
            & (~prepared.index.isin(used))
            & (prepared["_name_key"] == name)
        ]

        for candidate_idx, candidate in candidates.iterrows():
            candidate_lat = candidate.get("Широта")
            candidate_lon = candidate.get("Долгота")

            if pd.isna(candidate_lat) or pd.isna(candidate_lon):
                continue

            distance = _distance_m(
                float(lat),
                float(lon),
                float(candidate_lat),
                float(candidate_lon),
            )

            if distance <= distance_threshold_m:
                group.append(candidate_idx)
                used.add(candidate_idx)

        groups.append(group)

    rows: list[pd.Series] = []

    for group in groups:
        subset = prepared.loc[group].drop(columns=["_name_key"], errors="ignore")
        aggregated = _aggregate_duplicates(subset)

        if not aggregated.empty:
            rows.append(aggregated.iloc[0])

    if not rows:
        return prepared.drop(columns=["_name_key"], errors="ignore")

    return pd.DataFrame(rows).reset_index(drop=True)


def _ensure_required_fields(joined: pd.DataFrame) -> pd.DataFrame:
    """Добавляет обязательные поля для итогового датасета."""
    result = joined.copy()

    if "Минут_пешком" in result.columns:
        result["travel_time_min"] = pd.to_numeric(
            result["Минут_пешком"],
            errors="coerce",
        ).astype("Int64")
    elif "travel_time_min" not in result.columns:
        result["travel_time_min"] = pd.NA

    if "Зона_доступности" in result.columns:
        result["accessibility_zone"] = result["Зона_доступности"]
    elif "accessibility_zone" not in result.columns:
        result["accessibility_zone"] = pd.NA

    if "Категория_2GIS" not in result.columns:
        if "Категория" in result.columns:
            result["Категория_2GIS"] = result["Категория"]
        else:
            result["Категория_2GIS"] = "Прочее"

    if "functional_category" not in result.columns:
        result["functional_category"] = "Прочее"

    if "criticality_score" not in result.columns:
        result["criticality_score"] = 0

    if "source_category_2gis" not in result.columns:
        result["source_category_2gis"] = result["Категория_2GIS"]

    if "validation_status" not in result.columns:
        result["validation_status"] = "ok"

    if "classification_status" not in result.columns:
        result["classification_status"] = "not_mapped"

    if "category" not in result.columns:
        result["category"] = result["Категория_2GIS"]

    if "Категория" not in result.columns:
        result["Категория"] = result["Категория_2GIS"]

    return result


def deduplicate_transport_stops(df: pd.DataFrame) -> pd.DataFrame:
    """Объединяет физические остановочные комплексы в одну строку.

    Остановки считаются как остановочные комплексы, а не как отдельные
    поисковые выдачи по автобусу, трамваю, троллейбусу или станции.
    """
    if df.empty or "functional_category" not in df.columns:
        return df

    stops_mask = df["functional_category"].eq("Транспорт")
    stops = df[stops_mask].copy()
    other = df[~stops_mask].copy()

    if stops.empty:
        return df

    for column in [
        "Название",
        "Адрес",
        "Зона_доступности",
        "accessibility_zone",
        "Тип_транспортного_объекта",
        "Поисковый_запрос",
    ]:
        if column not in stops.columns:
            stops[column] = pd.NA

    stops["_stop_key"] = (
        stops["Название"].map(_normalize_key)
        + "|"
        + stops["Адрес"].map(_normalize_key)
    )

    empty_key = stops["_stop_key"].str.strip("|").eq("")
    stops.loc[empty_key, "_stop_key"] = stops.loc[empty_key, "Тип_транспортного_объекта"].map(_normalize_key)

    aggregation = _aggregation_map(stops)
    merged_stops = stops.groupby("_stop_key", as_index=False).agg(aggregation)
    merged_stops = merged_stops.drop(columns=["_stop_key"], errors="ignore")

    merged_stops["Категория_2GIS"] = "Остановка общественного транспорта"
    merged_stops["Категория"] = "Остановка общественного транспорта"
    merged_stops["category"] = "Остановка общественного транспорта"
    merged_stops["functional_category"] = "Транспорт"

    result = pd.concat([other, merged_stops], ignore_index=True)

    if isinstance(df, gpd.GeoDataFrame):
        source_crs = df.crs or "EPSG:4326"
        return gpd.GeoDataFrame(result, geometry="geometry", crs=source_crs)

    return result


def deduplicate_same_poi(df: pd.DataFrame) -> pd.DataFrame:
    """Убирает повторы одного POI после привязки к изохронам."""
    if df.empty:
        return df

    aggregated = _aggregate_duplicates(df)
    aggregated = _deduplicate_same_name_nearby(aggregated, distance_threshold_m=50.0)

    if isinstance(df, gpd.GeoDataFrame):
        source_crs = df.crs or "EPSG:4326"
        return gpd.GeoDataFrame(aggregated, geometry="geometry", crs=source_crs)

    return aggregated


def attach_to_isochrones(
    pois: gpd.GeoDataFrame,
    isochrones: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Привязывает POI к непересекающимся зонам доступности.

    Один объект может попасть только в одну зону: 0–5, 5–10 или 10–15 минут.
    Если из-за геометрии объект пересекает несколько зон, сохраняется ближайшая
    зона с минимальным ``Минут_пешком``.
    """
    empty_columns = [
        "Минут_пешком",
        "travel_time_min",
        "От_минут",
        "До_минут",
        "Зона_доступности",
        "accessibility_zone",
        "Диапазон_доступности",
        "Название",
        "Категория_2GIS",
        "Категория",
        "functional_category",
        "criticality_score",
        "source_category_2gis",
        "validation_status",
        "geometry",
    ]

    if pois is None or pois.empty:
        return gpd.GeoDataFrame(columns=empty_columns, geometry="geometry", crs="EPSG:4326")

    points = _prepare_points_layer(pois)
    polygons = _prepare_isochrone_layer(isochrones)

    join_columns = [
        "minutes",
        "from_minutes",
        "to_minutes",
        "range_label",
        "range_label_ru",
        "geometry",
    ]

    joined = gpd.sjoin(
        points,
        polygons[join_columns],
        how="left",
        predicate="within",
    ).drop(columns=["index_right"], errors="ignore")

    joined = joined.rename(
        columns={
            "minutes": "Минут_пешком",
            "from_minutes": "От_минут",
            "to_minutes": "До_минут",
            "range_label": "Диапазон_доступности",
            "range_label_ru": "Зона_доступности",
        }
    )

    joined = joined.dropna(subset=["Минут_пешком"]).copy()

    if joined.empty:
        return gpd.GeoDataFrame(columns=empty_columns, geometry="geometry", crs="EPSG:4326")

    joined["Минут_пешком"] = pd.to_numeric(
        joined["Минут_пешком"],
        errors="coerce",
    ).astype("Int64")

    joined = joined.sort_values(["Минут_пешком"]).reset_index(drop=True)
    joined = _ensure_required_fields(joined)

    joined["_base_poi_key"] = joined.apply(_deduplication_key, axis=1)

    joined = (
        joined.sort_values(["_base_poi_key", "Минут_пешком"])
        .drop_duplicates(subset=["_base_poi_key"], keep="first")
        .drop(columns=["_base_poi_key"], errors="ignore")
        .reset_index(drop=True)
    )

    joined = _ensure_required_fields(joined)
    joined = deduplicate_transport_stops(joined)
    joined = deduplicate_same_poi(joined)
    joined = _ensure_required_fields(joined)

    return gpd.GeoDataFrame(joined, geometry="geometry", crs=points.crs or "EPSG:4326").reset_index(drop=True)


def attach_isochrone_counts(
    pois: gpd.GeoDataFrame,
    isochrones: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Считает количество объектов по непересекающимся зонам и категориям."""
    joined = attach_to_isochrones(pois, isochrones)

    output_columns = [
        "Минут_пешком",
        "travel_time_min",
        "От_минут",
        "До_минут",
        "Зона_доступности",
        "accessibility_zone",
        "Категория_2GIS",
        "functional_category",
        "criticality_score",
        "Количество",
    ]

    if joined.empty or "Минут_пешком" not in joined.columns:
        return pd.DataFrame(columns=output_columns)

    for column in output_columns:
        if column not in joined.columns and column != "Количество":
            joined[column] = pd.NA

    result = (
        joined.dropna(subset=["Минут_пешком"])
        .groupby(
            [
                "Минут_пешком",
                "travel_time_min",
                "От_минут",
                "До_минут",
                "Зона_доступности",
                "accessibility_zone",
                "Категория_2GIS",
                "functional_category",
                "criticality_score",
            ],
            dropna=False,
        )
        .size()
        .reset_index(name="Количество")
        .sort_values(["Минут_пешком", "Количество", "Категория_2GIS"], ascending=[True, False, True])
        .reset_index(drop=True)
    )

    result["Категория"] = result["Категория_2GIS"]

    return result[output_columns]


def build_poi_details_by_isochrones(
    pois: gpd.GeoDataFrame,
    isochrones: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Формирует детальный список POI по непересекающимся зонам доступности."""
    joined = attach_to_isochrones(pois, isochrones)

    output_columns = [
        "Название",
        "Адрес",
        "Категория_2GIS",
        "functional_category",
        "Минут_пешком",
        "От_минут",
        "До_минут",
        "Зона_доступности",
        "accessibility_zone",
        "Рейтинг",
        "Количество_отзывов",
        "Источник",
        "criticality_score",
        "classification_rule_id",
        "classification_status",
        "validation_status",
        "dgis_id",
        "fid",
        "Широта",
        "Долгота",
    ]

    if joined.empty:
        return pd.DataFrame(columns=output_columns)

    joined = _ensure_required_fields(joined)

    for column in output_columns:
        if column not in joined.columns:
            joined[column] = pd.NA

    joined = joined.dropna(subset=["Минут_пешком"]).copy()

    joined["Минут_пешком"] = pd.to_numeric(joined["Минут_пешком"], errors="coerce").astype("Int64")

    for column in ["От_минут", "До_минут"]:
        joined[column] = pd.to_numeric(joined[column], errors="coerce").astype("Int64")

    joined["criticality_score"] = pd.to_numeric(
        joined["criticality_score"],
        errors="coerce",
    ).fillna(0).astype(int)

    if "Рейтинг" in joined.columns:
        joined["Рейтинг"] = pd.to_numeric(joined["Рейтинг"], errors="coerce")

    if "Количество_отзывов" in joined.columns:
        joined["Количество_отзывов"] = pd.to_numeric(
            joined["Количество_отзывов"],
            errors="coerce",
        ).astype("Int64")

    return (
        joined[output_columns]
        .sort_values(
            [
                "Минут_пешком",
                "criticality_score",
                "Категория_2GIS",
                "Количество_отзывов",
                "Рейтинг",
                "Название",
            ],
            ascending=[True, False, True, False, False, True],
            na_position="last",
        )
        .reset_index(drop=True)
    )