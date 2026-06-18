from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import geopandas as gpd
from shapely import wkt
from shapely.geometry.base import BaseGeometry

from geo_analyzer.core.logger import get_logger
from geo_analyzer.ingestion.dgis.client import DGISClient

logger = get_logger("geo_analyzer.geometry.isochrones")


def _ensure_iterable_minutes(minutes: Iterable[int]) -> list[int]:
    """Нормализует список верхних границ изохрон.

    Для проекта базовая логика — 5/10/15 минут, но функция оставлена
    универсальной: она принимает любой возрастающий набор положительных минут.
    """
    normalized = sorted({int(value) for value in minutes if int(value) > 0})
    if not normalized:
        raise ValueError("Список изохрон пустой или содержит некорректные значения.")
    if len(normalized) > 5:
        raise ValueError("2GIS Isochrone API принимает не более 5 значений duration.")
    return normalized


def _extract_isochrones(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Достаёт список изохрон из возможных форматов ответа 2GIS."""
    if isinstance(data.get("isochrones"), list):
        return data["isochrones"]

    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("isochrones"), list):
        return result["isochrones"]

    return []


def _make_exclusive_rings(cumulative: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Преобразует накопительные изохроны в непересекающиеся кольца.

    2GIS возвращает зоны по принципу "доступно за N минут". Для аналитики
    инфраструктуры это даёт задвоение: объект из 0–5 минут одновременно
    попадает в 0–10 и 0–15. Здесь мы вычитаем предыдущую меньшую изохрону
    из следующей и получаем классы: 0–5, 5–10, 10–15.
    """
    if cumulative.empty:
        return cumulative

    projected = cumulative.to_crs(epsg=3857).sort_values("minutes").reset_index(drop=True)

    rows: list[dict[str, Any]] = []
    previous_geometry: BaseGeometry | None = None
    previous_minutes = 0

    for _, row in projected.iterrows():
        current_minutes = int(row["minutes"])
        current_geometry = row.geometry

        if current_geometry is None or current_geometry.is_empty:
            continue

        if previous_geometry is None or previous_geometry.is_empty:
            ring_geometry = current_geometry
        else:
            # buffer(0) чинит часть самопересечений, которые иногда приходят
            # из API и мешают операции difference.
            ring_geometry = current_geometry.buffer(0).difference(previous_geometry.buffer(0))

        if ring_geometry is None or ring_geometry.is_empty:
            logger.warning(
                "Кольцо %s-%s минут получилось пустым. Проверьте геометрию изохрон.",
                previous_minutes,
                current_minutes,
            )
        else:
            rows.append(
                {
                    "minutes": current_minutes,
                    "from_minutes": previous_minutes,
                    "to_minutes": current_minutes,
                    "isochrone_type": "exclusive_ring",
                    "range_label": f"{previous_minutes}-{current_minutes}",
                    "range_label_ru": f"{previous_minutes}–{current_minutes} мин",
                    "geometry": ring_geometry,
                }
            )

        previous_geometry = current_geometry if previous_geometry is None else current_geometry.union(previous_geometry)
        previous_minutes = current_minutes

    if not rows:
        raise RuntimeError("Не удалось построить непересекающиеся кольца изохрон.")

    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3857").to_crs(epsg=4326)


def build_isochrones(
    latitude: float,
    longitude: float,
    graph_dist_m: int,
    isochrone_minutes: Iterable[int],
    walk_speed_kph: float,
) -> gpd.GeoDataFrame:
    """Строит пешеходные изохроны через 2GIS и возвращает непересекающиеся зоны.

    На входе 2GIS отдаёт накопительные полигоны: 0–5, 0–10, 0–15 минут.
    На выходе проект получает кольца: 0–5, 5–10, 10–15 минут. Это нужно,
    чтобы один и тот же объект инфраструктуры не учитывался сразу в нескольких
    временных классах.

    Args:
        latitude: Широта анализируемой точки.
        longitude: Долгота анализируемой точки.
        graph_dist_m: Радиус графа. Сохранён для совместимости.
        isochrone_minutes: Верхние границы зон доступности.
        walk_speed_kph: Скорость пешехода. Сохранена для совместимости.

    Returns:
        GeoDataFrame с колонками minutes, from_minutes, to_minutes,
        range_label, range_label_ru и geometry.
    """
    del graph_dist_m
    del walk_speed_kph

    minutes = _ensure_iterable_minutes(isochrone_minutes)
    client = DGISClient()

    logger.info("Строим накопительные изохроны через 2GIS: %s", minutes)

    data = client.post_routing(
        "/isochrone/2.0.0",
        json_body={
            "start": {"lat": latitude, "lon": longitude},
            "durations": [minute * 60 for minute in minutes],
            "reverse": False,
            "transport": "walking",
        },
    )

    raw_isochrones = _extract_isochrones(data)
    if not raw_isochrones:
        raise RuntimeError("2GIS Isochrone API не вернул изохроны.")

    rows: list[dict[str, Any]] = []
    for item in raw_isochrones:
        geometry_wkt = item.get("geometry")
        duration_sec = item.get("duration")

        if not geometry_wkt or duration_sec is None:
            continue

        try:
            geometry = wkt.loads(str(geometry_wkt))
        except Exception as exc:
            logger.warning("Не удалось распарсить геометрию изохроны: %s", exc)
            continue

        rows.append(
            {
                "minutes": int(round(float(duration_sec) / 60)),
                "geometry": geometry,
            }
        )

    if not rows:
        raise RuntimeError("Не удалось распарсить изохроны из ответа 2GIS.")

    cumulative = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    cumulative = cumulative.sort_values("minutes").drop_duplicates(subset=["minutes"]).reset_index(drop=True)

    rings = _make_exclusive_rings(cumulative)
    logger.info("Построены непересекающиеся зоны изохрон: %s", rings["range_label"].tolist())
    return rings
