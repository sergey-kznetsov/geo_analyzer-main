from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd


def _score_0_10(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 2)))


def _area_km2(geometry: Any) -> float:
    if geometry is None or getattr(geometry, "is_empty", True):
        return 0.0

    series = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(epsg=3857)
    return float(series.area.iloc[0] / 1_000_000)


def _category_column(df: pd.DataFrame) -> str | None:
    for column in ["Категория_2GIS", "Категория", "category", "functional_category"]:
        if column in df.columns:
            return column
    return None


def _count_for_minute(poi_counts_by_iso: pd.DataFrame | None, minutes: int) -> int:
    if poi_counts_by_iso is None or poi_counts_by_iso.empty:
        return 0
    if "Минут_пешком" not in poi_counts_by_iso.columns or "Количество" not in poi_counts_by_iso.columns:
        return 0

    subset = poi_counts_by_iso[pd.to_numeric(poi_counts_by_iso["Минут_пешком"], errors="coerce") == minutes]
    if subset.empty:
        return 0
    return int(pd.to_numeric(subset["Количество"], errors="coerce").fillna(0).sum())


def _categories_for_minute(poi_counts_by_iso: pd.DataFrame | None, minutes: int) -> int:
    if poi_counts_by_iso is None or poi_counts_by_iso.empty:
        return 0
    if "Минут_пешком" not in poi_counts_by_iso.columns:
        return 0

    category_col = _category_column(poi_counts_by_iso)
    if category_col is None:
        return 0

    subset = poi_counts_by_iso[pd.to_numeric(poi_counts_by_iso["Минут_пешком"], errors="coerce") == minutes]
    if subset.empty:
        return 0
    return int(subset[category_col].dropna().astype(str).str.strip().replace("", pd.NA).dropna().nunique())


def build_network_metrics(
    isochrones: gpd.GeoDataFrame,
    poi_counts_by_iso: pd.DataFrame,
) -> pd.DataFrame:
    """Считает сетевые метрики в единой шкале 0-10.

    Раньше часть строк была абсолютными значениями, а часть — индексом 0-100.
    Для отчёта аналитикам все значения приводятся к 0-10, а исходный смысл
    показателя переносится в пояснение.
    """
    output_columns = ["Метрика", "Значение", "Пояснение", "Шкала_оценки"]

    if isochrones is None or isochrones.empty:
        return pd.DataFrame(columns=output_columns)

    iso = isochrones.copy()
    if iso.crs is None:
        iso = iso.set_crs(epsg=4326)
    elif str(iso.crs).upper() != "EPSG:4326":
        iso = iso.to_crs(epsg=4326)

    iso_10 = iso[iso["minutes"] == 10] if "minutes" in iso.columns else gpd.GeoDataFrame()
    area_10 = _area_km2(iso_10.iloc[0].geometry) if not iso_10.empty else 0.0

    poi_5 = _count_for_minute(poi_counts_by_iso, 5)
    poi_10 = _count_for_minute(poi_counts_by_iso, 10)
    poi_15 = _count_for_minute(poi_counts_by_iso, 15)
    categories_10 = _categories_for_minute(poi_counts_by_iso, 10)
    density_10 = (poi_10 / area_10) if area_10 > 0 else 0.0

    poi_5_score = _score_0_10(poi_5 / 35 * 10)
    poi_10_score = _score_0_10(poi_10 / 95 * 10)
    poi_15_score = _score_0_10(poi_15 / 150 * 10)
    categories_score = _score_0_10(categories_10 / 20 * 10)
    area_score = _score_0_10(area_10 / 2.5 * 10)
    density_score = _score_0_10(density_10 / 180 * 10)

    transport_access_score = _score_0_10(
        poi_10_score * 0.42
        + categories_score * 0.30
        + density_score * 0.18
        + area_score * 0.10
    )

    explanation = (
        "Сетевые метрики рассчитаны по данным 2GIS и приведены к шкале 0-10. "
        "Абсолютные значения используются внутри расчёта, но в отчёте выводится "
        "аналитическая оценка: чем выше, тем сильнее связанность и насыщенность зоны."
    )

    rows = [
        {
            "Метрика": "Доступность POI за 5 минут, из 10",
            "Значение": poi_5_score,
            "Пояснение": f"{explanation} Исходно найдено {poi_5} POI в зоне 0-5 минут.",
            "Шкала_оценки": "0-10",
        },
        {
            "Метрика": "Доступность POI за 10 минут, из 10",
            "Значение": poi_10_score,
            "Пояснение": f"{explanation} Исходно найдено {poi_10} POI в зоне 5-10 минут.",
            "Шкала_оценки": "0-10",
        },
        {
            "Метрика": "Доступность POI за 15 минут, из 10",
            "Значение": poi_15_score,
            "Пояснение": f"{explanation} Исходно найдено {poi_15} POI в зоне 10-15 минут.",
            "Шкала_оценки": "0-10",
        },
        {
            "Метрика": "Разнообразие категорий в 10 минутах, из 10",
            "Значение": categories_score,
            "Пояснение": f"{explanation} Исходно найдено {categories_10} категорий 2GIS в зоне 5-10 минут.",
            "Шкала_оценки": "0-10",
        },
        {
            "Метрика": "Покрытие 10-минутной изохроны, из 10",
            "Значение": area_score,
            "Пояснение": f"{explanation} Площадь 10-минутной зоны — {round(area_10, 4)} км².",
            "Шкала_оценки": "0-10",
        },
        {
            "Метрика": "Плотность POI в 10 минутах, из 10",
            "Значение": density_score,
            "Пояснение": f"{explanation} Исходная плотность — {round(density_10, 2)} POI/км².",
            "Шкала_оценки": "0-10",
        },
        {
            "Метрика": "Индекс транспортной доступности, из 10",
            "Значение": transport_access_score,
            "Пояснение": explanation,
            "Шкала_оценки": "0-10",
        },
    ]

    return pd.DataFrame(rows, columns=output_columns)
