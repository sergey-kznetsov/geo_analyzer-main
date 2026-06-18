from __future__ import annotations

from pathlib import Path
from typing import Any

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from shapely.geometry import Point


_MARKERS = ["o", "s", "^", "D", "P", "X", "v", "*", "h", "p", "8", "."]
_MAX_LABELS = 22
_MAX_LEGEND_ITEMS = 18

_PRIORITY_FUNCTIONS = {
    "Досуг и городское притяжение",
    "Транспорт",
    "Образование и развитие",
}


def _prepare_points_layer(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Готовит слой точек для карты.

    Поддерживает два формата:
    - GeoDataFrame с geometry;
    - обычный DataFrame с колонками Широта/Долгота.
    """
    if df is None or df.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")

    data = df.copy()

    if isinstance(data, gpd.GeoDataFrame) and "geometry" in data.columns:
        prepared = data.copy()

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

    if not {"Широта", "Долгота"}.issubset(data.columns):
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")

    data["Широта"] = pd.to_numeric(data["Широта"], errors="coerce")
    data["Долгота"] = pd.to_numeric(data["Долгота"], errors="coerce")
    data = data.dropna(subset=["Широта", "Долгота"]).copy()

    if data.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")

    geometry = [Point(lon, lat) for lat, lon in zip(data["Широта"], data["Долгота"])]

    return gpd.GeoDataFrame(data, geometry=geometry, crs="EPSG:4326")


def _category_column(points: gpd.GeoDataFrame) -> str:
    """Возвращает колонку категории для легенды."""
    if "Категория_2GIS" in points.columns:
        return "Категория_2GIS"

    if "Категория" in points.columns:
        return "Категория"

    if "functional_category" in points.columns:
        return "functional_category"

    points["Категория_2GIS"] = "Прочее"
    return "Категория_2GIS"


def _style_for_category(index: int) -> dict[str, Any]:
    """Возвращает стиль маркера."""
    return {
        "marker": _MARKERS[index % len(_MARKERS)],
        "size": 34 if index % len(_MARKERS) != 7 else 70,
    }


def _draw_isochrone_layer(ax, isochrones: gpd.GeoDataFrame | None) -> None:
    """Рисует границы изохрон."""
    if isochrones is None or isochrones.empty:
        return

    iso = isochrones.copy()

    if iso.crs is None:
        iso = iso.set_crs(epsg=4326)
    elif str(iso.crs).upper() != "EPSG:4326":
        iso = iso.to_crs(epsg=4326)

    iso = iso.to_crs(epsg=3857)

    for _, row in iso.sort_values("minutes").iterrows():
        label = row.get("range_label_ru") or row.get("Зона_доступности") or f"до {int(row.get('minutes', 0))} мин"

        gpd.GeoSeries([row.geometry], crs=iso.crs).boundary.plot(
            ax=ax,
            linewidth=1.4,
            linestyle="--",
            alpha=0.78,
            label=f"Изохрона {label}",
        )

        gpd.GeoSeries([row.geometry], crs=iso.crs).plot(
            ax=ax,
            alpha=0.08,
            linewidth=0,
        )


def _draw_approximate_radius_layer(ax, latitude: float | None, longitude: float | None) -> None:
    """Fallback: рисует примерные радиусы, если изохрон нет."""
    if latitude is None or longitude is None:
        return

    center = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326").to_crs(epsg=3857).iloc[0]

    for minutes in [5, 10, 15]:
        circle = center.buffer(minutes * 83)

        gpd.GeoSeries([circle], crs="EPSG:3857").boundary.plot(
            ax=ax,
            linewidth=1.2,
            linestyle="--",
            alpha=0.6,
            label=f"Примерный радиус {minutes} мин",
        )


def plot_base_map(latitude: float, longitude: float, output_path: Path) -> None:
    """Строит базовую карту точки анализа."""
    point_gdf = gpd.GeoDataFrame(
        [{"name": "Точка анализа"}],
        geometry=[Point(longitude, latitude)],
        crs="EPSG:4326",
    ).to_crs(epsg=3857)

    fig, ax = plt.subplots(figsize=(10, 10))
    point_gdf.plot(ax=ax, markersize=120, marker="*")

    try:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron)
    except Exception:
        pass

    ax.set_title("Базовая карта точки анализа")
    ax.set_axis_off()

    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _annotate_points(ax, points_3857: gpd.GeoDataFrame) -> None:
    """Подписывает ограниченное количество важных точек."""
    if points_3857.empty:
        return

    data = points_3857.copy()

    if "functional_category" in data.columns:
        candidates = data[data["functional_category"].isin(_PRIORITY_FUNCTIONS)].copy()
    else:
        candidates = data.copy()

    if candidates.empty:
        candidates = data.copy()

    if "Рейтинг" in candidates.columns:
        candidates["rating_sort"] = pd.to_numeric(candidates["Рейтинг"], errors="coerce").fillna(0)
    else:
        candidates["rating_sort"] = 0

    if "criticality_score" in candidates.columns:
        candidates["criticality_sort"] = pd.to_numeric(candidates["criticality_score"], errors="coerce").fillna(0)
    else:
        candidates["criticality_sort"] = 0

    candidates = candidates.sort_values(
        ["criticality_sort", "rating_sort", "Название"],
        ascending=[False, False, True],
    ).head(_MAX_LABELS)

    for _, row in candidates.iterrows():
        geom = row.geometry

        if geom is None:
            continue

        name = str(row.get("Название") or "").strip()

        if not name:
            continue

        ax.annotate(
            name,
            xy=(geom.x, geom.y),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=7,
            alpha=0.92,
            bbox={
                "facecolor": "white",
                "alpha": 0.65,
                "edgecolor": "none",
                "pad": 0.8,
            },
        )


def _apply_legend(ax, title: str = "Категории 2GIS") -> None:
    """Рисует легенду без дублей."""
    handles, labels = ax.get_legend_handles_labels()

    unique: dict[str, object] = {}

    for handle, label in zip(handles, labels):
        if not label or label.startswith("_"):
            continue

        if label not in unique:
            unique[label] = handle

    if not unique:
        return

    items = list(unique.items())[:_MAX_LEGEND_ITEMS]
    labels = [item[0] for item in items]
    handles = [item[1] for item in items]

    ncol = 2 if len(items) <= 12 else 3

    ax.legend(
        handles,
        labels,
        loc="upper left",
        fontsize=7,
        ncol=ncol,
        title=title,
        title_fontsize=8,
        framealpha=0.86,
    )


def _plot_points_by_category(ax, points_3857: gpd.GeoDataFrame) -> None:
    """Рисует точки по категориям 2GIS."""
    if points_3857.empty:
        return

    category_col = _category_column(points_3857)

    categories = (
        points_3857[category_col]
        .fillna("Прочее")
        .astype(str)
        .sort_values()
        .unique()
        .tolist()
    )

    for index, category in enumerate(categories):
        subset = points_3857[points_3857[category_col].astype(str) == str(category)].copy()

        if subset.empty:
            continue

        style = _style_for_category(index)

        subset.plot(
            ax=ax,
            markersize=style["size"],
            marker=style["marker"],
            label=category,
            alpha=0.84,
        )


def plot_isochrones(
    isochrones,
    latitude: float,
    longitude: float,
    pois,
    output_path: Path,
) -> None:
    """Строит карту изохрон и объектов внутри них."""
    point_gdf = gpd.GeoDataFrame(
        [{"name": "Точка анализа"}],
        geometry=[Point(longitude, latitude)],
        crs="EPSG:4326",
    )

    fig, ax = plt.subplots(figsize=(12, 12))

    _draw_isochrone_layer(ax, isochrones)

    plotted_points = gpd.GeoDataFrame()

    points = _prepare_points_layer(pois)
    if not points.empty:
        plotted_points = points.to_crs(epsg=3857)
        _plot_points_by_category(ax, plotted_points)

    point_gdf.to_crs(epsg=3857).plot(
        ax=ax,
        markersize=150,
        marker="*",
        label="Точка анализа",
    )

    _annotate_points(ax, plotted_points)

    try:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron)
    except Exception:
        pass

    _apply_legend(ax)
    ax.set_title("Изохроны 0–5 / 5–10 / 10–15 минут и POI внутри зон")
    ax.set_axis_off()

    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_poi_map(
    pois,
    output_path: Path,
    latitude: float | None = None,
    longitude: float | None = None,
    isochrones=None,
) -> None:
    """Строит карту инфраструктуры внутри изохрон."""
    fig, ax = plt.subplots(figsize=(13, 13))

    _draw_isochrone_layer(ax, isochrones)

    if isochrones is None or getattr(isochrones, "empty", True):
        _draw_approximate_radius_layer(ax, latitude, longitude)

    plotted_points = gpd.GeoDataFrame()

    points = _prepare_points_layer(pois)
    if not points.empty:
        plotted_points = points.to_crs(epsg=3857)
        _plot_points_by_category(ax, plotted_points)

    if latitude is not None and longitude is not None:
        center = gpd.GeoDataFrame(
            [{"name": "Точка анализа"}],
            geometry=[Point(longitude, latitude)],
            crs="EPSG:4326",
        ).to_crs(epsg=3857)

        center.plot(
            ax=ax,
            markersize=150,
            marker="*",
            label="Точка анализа",
        )

    _annotate_points(ax, plotted_points)

    try:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron)
    except Exception:
        pass

    _apply_legend(ax)
    ax.set_title("Инфраструктура внутри изохрон по категориям 2GIS")
    ax.set_axis_off()

    plt.tight_layout()
    plt.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)