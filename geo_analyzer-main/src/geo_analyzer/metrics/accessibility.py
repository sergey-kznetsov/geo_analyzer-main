from __future__ import annotations

from typing import Any

import pandas as pd


TRANSPORT_FUNCTION = "Транспорт"
LEISURE_FUNCTION = "Досуг и городское притяжение"

STOP_KEYWORDS = {
    "транспорт",
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

STOP_RUBRIC_IDS = {"450"}


def _score(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 2)))


def _zone_label(minutes: int) -> str:
    if minutes <= 5:
        return "0–5 мин"
    if minutes <= 10:
        return "5–10 мин"
    return "10–15 мин"


def _class(score: float) -> str:
    if score < 3:
        return "Слабая"
    if score < 6:
        return "Средняя"
    if score < 8:
        return "Хорошая"
    return "Сильная"


def _empty_accessibility_snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "Зона_доступности",
            "Минут_пешком",
            "Количество_POI",
            "Количество_категорий",
            "Остановочных_комплексов",
            "Точек_притяжения_городского_масштаба",
            "Пешая_доступность_из_10",
            "Остановочная_доступность_из_10",
            "Авто_доступность_до_центра_из_10",
            "Итоговая_доступность_из_10",
            "Пешая_доступность_из_100",
            "Остановочная_доступность_из_100",
            "Авто_доступность_до_центра_из_100",
            "Итоговая_доступность_из_100",
            "Класс_доступности",
            "Авто_время_до_центра_мин",
            "Авто_расстояние_до_центра_км",
            "Пешком_до_центра_мин",
            "Пешком_до_центра_км",
            "Источник_авто_метрики",
            "Центр_города",
            "Город_центра",
            "Пояснение",
            "Шкала_оценки",
        ]
    )


def _safe_count(data: pd.DataFrame, column: str) -> int:
    if data.empty or column not in data.columns:
        return 0

    return int(data[column].dropna().nunique())


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_drive_time(drive_metrics: dict[str, Any] | None) -> float | None:
    if not drive_metrics:
        return None

    for key in ["drive_time_min", "avg_drive_time_min", "time_min", "duration_min"]:
        value = _safe_float(drive_metrics.get(key))
        if value is not None:
            return value

    return None


def _extract_drive_distance(drive_metrics: dict[str, Any] | None) -> float | None:
    if not drive_metrics:
        return None

    for key in ["drive_distance_km", "avg_drive_distance_km", "distance_km"]:
        value = _safe_float(drive_metrics.get(key))
        if value is not None:
            return value

    return None


def _drive_score_10(drive_metrics: dict[str, Any] | None) -> float:
    drive_time = _extract_drive_time(drive_metrics)

    if drive_time is None:
        return 0.0

    if drive_time <= 5:
        return 10.0
    if drive_time <= 10:
        return 8.5
    if drive_time <= 15:
        return 7.0
    if drive_time <= 20:
        return 5.5
    if drive_time <= 30:
        return 4.0
    if drive_time <= 45:
        return 2.5

    return 1.0


def _prepare_details(poi_details_by_iso: pd.DataFrame | None) -> pd.DataFrame:
    if poi_details_by_iso is None or poi_details_by_iso.empty:
        return pd.DataFrame()

    data = poi_details_by_iso.copy()

    defaults = {
        "Минут_пешком": 15,
        "Категория_2GIS": "Прочее",
        "Категория": "Прочее",
        "functional_category": "Прочее",
        "Название": pd.NA,
    }

    for column, default in defaults.items():
        if column not in data.columns:
            data[column] = default

    data["Минут_пешком"] = (
        pd.to_numeric(data["Минут_пешком"], errors="coerce")
        .fillna(15)
        .astype(int)
    )

    if "Категория_2GIS" not in data.columns or data["Категория_2GIS"].isna().all():
        data["Категория_2GIS"] = data["Категория"]

    data["Категория_2GIS"] = data["Категория_2GIS"].fillna("Прочее").astype(str)
    data["functional_category"] = data["functional_category"].fillna("Прочее").astype(str)

    return data


def _zone_subset(data: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return data[data["Минут_пешком"].eq(minutes)].copy()


def _pedestrian_score(poi_count: int, category_count: int, attraction_count: int) -> float:
    poi_component = min(poi_count / 40 * 5.5, 5.5)
    category_component = min(category_count / 10 * 3.5, 3.5)
    attraction_component = min(attraction_count / 3 * 1.0, 1.0)

    return _score(poi_component + category_component + attraction_component)


def _stop_score(stops_count: int) -> float:
    if stops_count <= 0:
        return 0.0

    if stops_count == 1:
        return 3.0

    if stops_count == 2:
        return 5.0

    if stops_count == 3:
        return 7.0

    if stops_count <= 5:
        return 8.5

    return 10.0


def _zone_weight(minutes: int) -> float:
    if minutes <= 5:
        return 1.00
    if minutes <= 10:
        return 0.82
    return 0.62


def _row_values_as_text(row: pd.Series, columns: list[str]) -> str:
    values: list[str] = []

    for column in columns:
        if column not in row.index:
            continue

        raw_value = row.get(column)

        if isinstance(raw_value, (list, tuple, set)):
            values.extend(str(item) for item in raw_value)
        else:
            values.append(str(raw_value))

    return " ".join(values).replace("ё", "е").lower()


def _is_stop_row(row: pd.Series) -> bool:
    text = _row_values_as_text(
        row,
        [
            "functional_category",
            "Категория_2GIS",
            "Категория",
            "category",
            "source_category_2gis",
            "source_categories_2gis",
            "rubrics_2gis",
            "category_groups_2gis",
            "rubric_id",
            "Название",
            "name",
        ],
    )

    if any(keyword in text for keyword in STOP_KEYWORDS):
        return True

    rubric_id = str(row.get("rubric_id", "")).strip().lower()
    if rubric_id in STOP_RUBRIC_IDS:
        return True

    return False


def _count_stops(zone: pd.DataFrame) -> int:
    if zone.empty:
        return 0

    transport_zone = zone[zone.apply(_is_stop_row, axis=1)].copy()

    if transport_zone.empty:
        return 0

    for column in ["dgis_id", "fid", "Название", "name"]:
        if column in transport_zone.columns:
            count = int(transport_zone[column].dropna().nunique())
            if count > 0:
                return count

    return int(len(transport_zone))


def _count_city_attractions(zone: pd.DataFrame) -> int:
    if zone.empty:
        return 0

    if "functional_category" not in zone.columns or "Категория_2GIS" not in zone.columns:
        return 0

    attraction_mask = (
        zone["functional_category"].eq(LEISURE_FUNCTION)
        & zone["Категория_2GIS"].astype(str).str.contains(
            (
                "парк|сквер|бульвар|набережная|торговый центр|тц|трц|"
                "театр|музей|кинотеатр|стадион|филармония|цирк"
            ),
            case=False,
            regex=True,
            na=False,
        )
    )

    attractions = zone[attraction_mask]

    if attractions.empty:
        return 0

    for column in ["dgis_id", "fid", "Название"]:
        if column in attractions.columns:
            count = int(attractions[column].dropna().nunique())
            if count > 0:
                return count

    return int(len(attractions))


def _drive_meta(drive_metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not drive_metrics:
        return {
            "Авто_время_до_центра_мин": None,
            "Авто_расстояние_до_центра_км": None,
            "Пешком_до_центра_мин": None,
            "Пешком_до_центра_км": None,
            "Источник_авто_метрики": None,
            "Центр_города": None,
            "Город_центра": None,
        }

    return {
        "Авто_время_до_центра_мин": _extract_drive_time(drive_metrics),
        "Авто_расстояние_до_центра_км": _extract_drive_distance(drive_metrics),
        "Пешком_до_центра_мин": _safe_float(drive_metrics.get("walk_time_min")),
        "Пешком_до_центра_км": _safe_float(drive_metrics.get("walk_distance_km")),
        "Источник_авто_метрики": drive_metrics.get("data_source"),
        "Центр_города": drive_metrics.get("center_name"),
        "Город_центра": drive_metrics.get("center_city"),
    }


def build_accessibility_snapshot(
    poi_counts_by_iso: pd.DataFrame | None,
    poi_details_by_iso: pd.DataFrame | None = None,
    drive_metrics: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Формирует снимок доступности по непересекающимся пешим изохронам.

    Разделение метрик:
    - пешая доступность — инфраструктура внутри пешей зоны;
    - остановочная доступность — остановки внутри пешей зоны;
    - авто-доступность — время на авто до центра;
    - итоговая доступность — агрегат отдельных показателей.
    """
    details = _prepare_details(poi_details_by_iso)

    if details.empty:
        return _empty_accessibility_snapshot()

    rows: list[dict[str, object]] = []

    drive_score = _drive_score_10(drive_metrics)
    drive_meta = _drive_meta(drive_metrics)

    for minutes in [5, 10, 15]:
        zone = _zone_subset(details, minutes)

        poi_count = int(len(zone))
        category_count = _safe_count(zone, "Категория_2GIS")
        stops_count = _count_stops(zone)
        attraction_count = _count_city_attractions(zone)

        pedestrian_score = _pedestrian_score(
            poi_count=poi_count,
            category_count=category_count,
            attraction_count=attraction_count,
        )

        stop_score = _stop_score(stops_count)

        total_score = _score(
            (
                pedestrian_score * 0.56
                + stop_score * 0.24
                + drive_score * 0.20
            )
            * _zone_weight(minutes)
        )

        row = {
            "Зона_доступности": _zone_label(minutes),
            "Минут_пешком": minutes,
            "Количество_POI": poi_count,
            "Количество_категорий": category_count,
            "Остановочных_комплексов": stops_count,
            "Точек_притяжения_городского_масштаба": attraction_count,
            "Пешая_доступность_из_10": pedestrian_score,
            "Остановочная_доступность_из_10": stop_score,
            "Авто_доступность_до_центра_из_10": drive_score,
            "Итоговая_доступность_из_10": total_score,
            "Пешая_доступность_из_100": round(pedestrian_score * 10, 2),
            "Остановочная_доступность_из_100": round(stop_score * 10, 2),
            "Авто_доступность_до_центра_из_100": round(drive_score * 10, 2),
            "Итоговая_доступность_из_100": round(total_score * 10, 2),
            "Класс_доступности": _class(total_score),
            "Пояснение": (
                "Пешая доступность считается по POI и категориям внутри конкретной "
                "непересекающейся изохроны. Остановки считаются отдельно и проходят "
                "дедубликацию как остановочные комплексы. Авто-доступность до центра "
                "не смешивается с остановками и входит в итоговый показатель отдельным весом."
            ),
            "Шкала_оценки": "0–3 слабая, 3–6 средняя, 6–8 хорошая, 8–10 сильная.",
        }

        row.update(drive_meta)
        rows.append(row)

    return pd.DataFrame(rows)