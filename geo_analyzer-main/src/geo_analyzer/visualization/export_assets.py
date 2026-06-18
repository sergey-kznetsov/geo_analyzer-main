from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import contextily as ctx
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from shapely.geometry import Point

from geo_analyzer.core.utils import clean_category_label, ensure_directory, safe_float, first_column


plt.rcParams["figure.dpi"] = 160
plt.rcParams["savefig.dpi"] = 160
plt.rcParams["font.family"] = "DejaVu Sans"


def _wrap_label(text: str, width: int = 28) -> str:
    words = str(text).split()
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        add_len = len(word) + (1 if current else 0)
        if current_len + add_len <= width:
            current.append(word)
            current_len += add_len
        else:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _analysis_point(result: dict[str, Any]) -> Point:
    meta = result.get("meta", {}) if isinstance(result.get("meta"), dict) else {}
    lat = safe_float(meta.get("latitude"))
    lon = safe_float(meta.get("longitude"))
    if lat is not None and lon is not None:
        return Point(lon, lat)
    context = result.get("context")
    if context is not None and getattr(context, "location", None) is not None:
        location = context.location
        lat = safe_float(getattr(location, "latitude", None))
        lon = safe_float(getattr(location, "longitude", None))
        if lat is not None and lon is not None:
            return Point(lon, lat)
    return Point(0, 0)


def _extract_minutes(label: Any, row: dict[str, Any]) -> int | None:
    for key in ["minutes", "Минут_пешком", "minute", "value", "to_min"]:
        value = row.get(key)
        try:
            if value is not None:
                return int(float(value))
        except Exception:
            continue
    text = str(label or row.get("Зона_доступности") or row.get("zone") or "").strip()
    if "0–5" in text or "0-5" in text:
        return 5
    if "5–10" in text or "5-10" in text:
        return 10
    if "10–15" in text or "10-15" in text:
        return 15
    return None


def _zone_label_from_minutes(minutes: Any) -> str:
    try:
        minutes = int(float(minutes))
    except Exception:
        return "Неизвестная зона"
    if minutes == 5:
        return "0–5 мин"
    if minutes == 10:
        return "5–10 мин"
    if minutes == 15:
        return "10–15 мин"
    return f"до {minutes} мин"


def _normalize_isochrones(value: Any) -> gpd.GeoDataFrame:
    if isinstance(value, gpd.GeoDataFrame):
        gdf = value.copy()
    elif isinstance(value, pd.DataFrame) and "geometry" in value.columns:
        gdf = gpd.GeoDataFrame(value.copy(), geometry="geometry", crs=getattr(value, "crs", None) or "EPSG:4326")
    elif isinstance(value, list):
        rows = []
        for item in value:
            if isinstance(item, dict) and item.get("geometry") is not None:
                label = item.get("zone_label") or item.get("label") or item.get("bucket") or ""
                rows.append({"zone_label": str(label), "minutes": _extract_minutes(label, item), "geometry": item.get("geometry")})
        gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    else:
        gdf = gpd.GeoDataFrame(columns=["zone_label", "minutes", "geometry"], geometry="geometry", crs="EPSG:4326")

    if gdf.empty:
        return gdf
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif str(gdf.crs).upper() != "EPSG:4326":
        gdf = gdf.to_crs(epsg=4326)
    if "minutes" not in gdf.columns:
        gdf["minutes"] = [None] * len(gdf)
    if "zone_label" not in gdf.columns:
        gdf["zone_label"] = [None] * len(gdf)
    gdf["minutes"] = [_extract_minutes(gdf.iloc[idx].get("zone_label"), gdf.iloc[idx].to_dict()) for idx in range(len(gdf))]
    gdf["zone_label"] = [_zone_label_from_minutes(item) for item in gdf["minutes"].tolist()]
    return gdf.dropna(subset=["geometry"]).sort_values("minutes")


def _normalize_pois(value: Any) -> gpd.GeoDataFrame:
    if isinstance(value, gpd.GeoDataFrame):
        gdf = value.copy()
    elif isinstance(value, pd.DataFrame):
        df = value.copy()
        if "geometry" in df.columns:
            gdf = gpd.GeoDataFrame(df, geometry="geometry", crs=getattr(value, "crs", None) or "EPSG:4326")
        else:
            lat_col = next((c for c in ["Широта", "latitude", "lat"] if c in df.columns), None)
            lon_col = next((c for c in ["Долгота", "longitude", "lon", "lng"] if c in df.columns), None)
            if lat_col and lon_col:
                df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
                df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
                df = df.dropna(subset=[lat_col, lon_col]).copy()
                geometry = [Point(lon, lat) for lat, lon in zip(df[lat_col], df[lon_col])]
                gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
            else:
                gdf = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")
    elif isinstance(value, list):
        return _normalize_pois(pd.DataFrame(value))
    else:
        gdf = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs="EPSG:4326")

    if gdf.empty:
        return gdf
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif str(gdf.crs).upper() != "EPSG:4326":
        gdf = gdf.to_crs(epsg=4326)
    gdf = gdf.dropna(subset=["geometry"]).copy()
    gdf = gdf[~gdf.geometry.is_empty].copy()
    if gdf.empty:
        return gdf

    category_column = next((c for c in ["Категория_2GIS", "clean_category_2gis", "source_category_2gis", "Категория", "functional_category"] if c in gdf.columns), None)
    gdf["plot_category"] = gdf[category_column].apply(_clean_category_label) if category_column else "Без категории"
    minutes_column = next((c for c in ["Минут_пешком", "minutes", "travel_time_min", "До_минут"] if c in gdf.columns), None)
    gdf["minutes"] = pd.to_numeric(gdf[minutes_column], errors="coerce") if minutes_column else pd.NA
    if "zone_label" not in gdf.columns:
        gdf["zone_label"] = gdf["minutes"].apply(_zone_label_from_minutes)
    return gdf


def _normalize_category_summary(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        df = value.copy()
    elif isinstance(value, list):
        df = pd.DataFrame(value)
    elif isinstance(value, dict):
        df = pd.DataFrame([{"Категория_2GIS": key, "Количество": val} for key, val in value.items()])
    else:
        df = pd.DataFrame()
    if df.empty:
        return pd.DataFrame(columns=["label", "count"])
    label_col = next((c for c in ["Категория_2GIS", "category", "label", "name"] if c in df.columns), None)
    count_col = next((c for c in ["Количество", "count", "value", "cnt"] if c in df.columns), None)
    if label_col is None or count_col is None:
        return pd.DataFrame(columns=["label", "count"])
    out = df[[label_col, count_col]].copy()
    out.columns = ["label", "count"]
    out["label"] = out["label"].apply(_clean_category_label)
    out["count"] = pd.to_numeric(out["count"], errors="coerce")
    out = out.dropna(subset=["count"])
    return out.sort_values(["count", "label"], ascending=[False, True])


def _normalize_quality_scores(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        df = value.copy()
        if df.empty:
            return pd.DataFrame(columns=["metric", "score"])
        metric_col = next((c for c in ["Метрика", "metric", "Показатель", "Название"] if c in df.columns), None)
        score_col = next((c for c in ["Оценка_из_10", "score", "value", "Оценка_из_100"] if c in df.columns), None)
        if metric_col is None or score_col is None:
            return pd.DataFrame(columns=["metric", "score"])
        out = df[[metric_col, score_col]].copy()
        out.columns = ["metric", "score"]
        out["score"] = pd.to_numeric(out["score"], errors="coerce")
        if score_col == "Оценка_из_100":
            out["score"] = out["score"] / 10
        out = out.dropna(subset=["metric", "score"])
        out["score"] = out["score"].clip(lower=0, upper=10)
        return out.sort_values("score", ascending=True).reset_index(drop=True)
    return pd.DataFrame(columns=["metric", "score"])


def _get_extent(point_gdf_3857: gpd.GeoDataFrame, isochrones_3857: gpd.GeoDataFrame | None, radius_m: float = 1700) -> tuple[float, float, float, float]:
    if isochrones_3857 is not None and not isochrones_3857.empty:
        minx, miny, maxx, maxy = isochrones_3857.total_bounds
        padx = max((maxx - minx) * 0.18, 220)
        pady = max((maxy - miny) * 0.18, 220)
        return minx - padx, maxx + padx, miny - pady, maxy + pady
    x = point_gdf_3857.geometry.iloc[0].x
    y = point_gdf_3857.geometry.iloc[0].y
    return x - radius_m, x + radius_m, y - radius_m, y + radius_m


def _apply_local_basemap(ax: plt.Axes, extent: tuple[float, float, float, float]) -> None:
    xmin, xmax, ymin, ymax = extent
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    try:
        ctx.add_basemap(ax, source=ctx.providers.CartoDB.Positron, crs="EPSG:3857", attribution_size=6, zoom=15)
    except Exception:
        pass
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)


def _plot_base_map(result: dict[str, Any], output_path: Path) -> None:
    point = _analysis_point(result)
    point_gdf = gpd.GeoDataFrame([{"geometry": point}], geometry="geometry", crs="EPSG:4326")
    point_3857 = point_gdf.to_crs(epsg=3857)
    isochrones = _normalize_isochrones(result.get("isochrones"))
    isochrones_3857 = isochrones.to_crs(epsg=3857) if not isochrones.empty else None
    extent = _get_extent(point_3857, isochrones_3857, radius_m=1600)
    fig, ax = plt.subplots(figsize=(8.2, 8.2))
    _apply_local_basemap(ax, extent)
    point_3857.plot(ax=ax, color="#1565C0", marker="*", markersize=170, zorder=5)
    ax.set_title("Базовая карта точки анализа", fontsize=16)
    ax.set_axis_off()
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def _plot_quality_scores(result: dict[str, Any], output_path: Path) -> None:
    df = _normalize_quality_scores(result.get("quality_scores"))
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(10.6, 6.2))
    ax.barh(df["metric"], df["score"])
    ax.set_xlim(0, 10)
    ax.set_xlabel("Баллы, 0–10")
    ax.set_title("Индексы качества локации")
    ax.grid(axis="x", alpha=0.2)
    for idx, value in enumerate(df["score"].tolist()):
        ax.text(value + 0.08, idx, f"{value:.1f}", va="center", fontsize=9)
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def _plot_top_categories(result: dict[str, Any], output_path: Path) -> None:
    df = _normalize_category_summary(result.get("category_summary"))
    if df.empty:
        return
    df = df.head(10).sort_values(["count", "label"], ascending=[True, False])
    df["label_wrapped"] = df["label"].apply(lambda x: _wrap_label(x, 28))
    fig, ax = plt.subplots(figsize=(11.2, 6.6))
    ax.barh(df["label_wrapped"], df["count"])
    ax.set_xlabel("Количество объектов")
    ax.set_title("Топ категорий POI")
    ax.grid(axis="x", alpha=0.2)
    for idx, value in enumerate(df["count"].tolist()):
        ax.text(value + 0.2, idx, f"{int(value)}", va="center", fontsize=9)
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def _plot_isochrones_map(result: dict[str, Any], output_path: Path) -> None:
    point = _analysis_point(result)
    point_gdf = gpd.GeoDataFrame([{"geometry": point}], geometry="geometry", crs="EPSG:4326")
    point_3857 = point_gdf.to_crs(epsg=3857)
    isochrones = _normalize_isochrones(result.get("isochrones"))
    if isochrones.empty:
        return
    isochrones_3857 = isochrones.to_crs(epsg=3857)
    extent = _get_extent(point_3857, isochrones_3857, radius_m=1600)
    palette = {"0–5 мин": ("#C8E6C9", "#2E7D32"), "5–10 мин": ("#BBDEFB", "#1565C0"), "10–15 мин": ("#E3F2FD", "#64B5F6")}
    fig, ax = plt.subplots(figsize=(8.6, 8.2))
    _apply_local_basemap(ax, extent)
    for label in ["10–15 мин", "5–10 мин", "0–5 мин"]:
        zone = isochrones_3857[isochrones_3857["zone_label"] == label]
        if zone.empty:
            continue
        fill, edge = palette.get(label, ("#E0E0E0", "#424242"))
        zone.plot(ax=ax, facecolor=fill, edgecolor=edge, linewidth=2, alpha=0.28, zorder=2)
    point_3857.plot(ax=ax, color="#D32F2F", marker="*", markersize=170, zorder=5)
    ax.set_title("Пешеходные изохроны 0–5 / 5–10 / 10–15 минут", fontsize=15)
    ax.set_axis_off()
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)



def export_visuals(result: dict[str, Any], images_dir: Path) -> dict[str, str]:
    images_dir = ensure_directory(images_dir)
    visuals: dict[str, str] = {}
    file_map = {
        "base_map": images_dir / "base_map.png",
        "isochrone_map": images_dir / "isochrone_map.png",
        "infrastructure_map": images_dir / "infrastructure_map.png",
        "quality_scores": images_dir / "quality_scores.png",
        "top_categories": images_dir / "top_categories.png",
    }
    tasks = [
        ("base_map", _plot_base_map),
        ("isochrone_map", _plot_isochrones_map),
        ("infrastructure_map", _plot_infrastructure_map),
        ("quality_scores", _plot_quality_scores),
        ("top_categories", _plot_top_categories),
    ]
    for key, builder in tasks:
        path = file_map[key]
        try:
            builder(result, path)
            if path.exists():
                visuals[key] = str(path)
        except Exception:
            continue
    visuals["quality_scores_chart"] = visuals.get("quality_scores", "")
    visuals["top_categories_chart"] = visuals.get("top_categories", "")
    visuals["base_map_path"] = visuals.get("base_map", "")
    visuals["isochrone_map_path"] = visuals.get("isochrone_map", "")
    visuals["infrastructure_map_path"] = visuals.get("infrastructure_map", "")
    manifest_path = images_dir / "visuals_manifest.json"
    try:
        manifest_path.write_text(json.dumps(visuals, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return visuals

def _plot_infrastructure_map(result: dict[str, Any], output_path: Path) -> None:
    """Карта POI внутри изохрон.

    Важно: зоны POI НЕ пересчитываются по геометрии карты.
    Для отображения используется готовая колонка Минут_пешком из poi_details_by_iso.
    Это та же логика, по которой строится таблица POI по изохронам.
    """
    import pandas as pd
    import geopandas as gpd
    import matplotlib.pyplot as plt

    point = _analysis_point(result)
    point_gdf = gpd.GeoDataFrame([{"geometry": point}], geometry="geometry", crs="EPSG:4326")

    isochrones = _normalize_isochrones(result.get("isochrones"))
    if isochrones.empty:
        return

    pois = _infrastructure_pois_from_result(result)

    point_3857 = point_gdf.to_crs(epsg=3857)
    isochrones_3857 = isochrones.to_crs(epsg=3857)

    if not pois.empty:
        pois_3857 = pois.to_crs(epsg=3857)
    else:
        pois_3857 = pois

    extent = _get_extent(point_3857, isochrones_3857, radius_m=1600)

    fig, ax = plt.subplots(figsize=(8.8, 9.2))
    _apply_local_basemap(ax, extent)

    fill_map = {
        15: "#E3F2FD",
        10: "#BBDEFB",
        5: "#C8E6C9",
    }
    edge_map = {
        15: "#64B5F6",
        10: "#1E88E5",
        5: "#1565C0",
    }

    for minutes in [15, 10, 5]:
        zone = isochrones_3857[isochrones_3857["minutes"] == minutes]
        if zone.empty:
            continue

        zone.plot(
            ax=ax,
            facecolor=fill_map.get(minutes, "#E0E0E0"),
            edgecolor=edge_map.get(minutes, "#1E88E5"),
            linewidth=1.8,
            alpha=0.12,
            zorder=1,
        )
        zone.boundary.plot(
            ax=ax,
            color=edge_map.get(minutes, "#1E88E5"),
            linewidth=1.8,
            linestyle="--" if minutes in [10, 15] else "-",
            zorder=2,
        )

    if not pois.empty:
        category_counts = pois["plot_category"].value_counts().head(6)
        top_categories = list(category_counts.index)

        other = pois_3857[~pois_3857["plot_category"].isin(top_categories)].copy()
        if not other.empty:
            other.plot(
                ax=ax,
                color="#9AA5B1",
                markersize=10,
                alpha=0.35,
                label=f"Прочие POI ({len(other)})",
                zorder=3,
            )

        colors = ["#D32F2F", "#2E7D32", "#1565C0", "#EF6C00", "#7B1FA2", "#00838F"]

        for idx, category in enumerate(top_categories):
            subset = pois_3857[pois_3857["plot_category"] == category]
            if subset.empty:
                continue

            subset.plot(
                ax=ax,
                color=colors[idx % len(colors)],
                markersize=20,
                alpha=0.9,
                label=f"{category} ({int(category_counts[category])})",
                zorder=4,
            )

        zone_counts = pois.groupby("minutes").size().to_dict()
        zone_text = [
            f"0–5 мин: {int(zone_counts.get(5, 0))} POI",
            f"5–10 мин: {int(zone_counts.get(10, 0))} POI",
            f"10–15 мин: {int(zone_counts.get(15, 0))} POI",
        ]

        ax.legend(
            title="Топ категорий POI",
            loc="lower left",
            fontsize=8,
            title_fontsize=9,
            frameon=True,
            facecolor="white",
            edgecolor="#D0D7E2",
        )
    else:
        zone_text = ["POI внутри изохрон не найдены"]

    point_3857.plot(ax=ax, color="#000000", marker="*", markersize=140, zorder=5)

    ax.text(
        0.02,
        0.02,
        "\n".join(zone_text),
        transform=ax.transAxes,
        fontsize=8.5,
        va="bottom",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#D0D7E2", alpha=0.95),
    )

    ax.set_title("Инфраструктура внутри пешеходных изохрон", fontsize=15)
    ax.set_axis_off()
    plt.tight_layout()
    fig.savefig(output_path, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def _infrastructure_pois_from_result(result: dict[str, Any]):
    """Берёт POI именно из готовой таблицы poi_details_by_iso.

    Эта таблица уже содержит корректное распределение по зонам:
    5 = 0–5 минут,
    10 = 5–10 минут,
    15 = 10–15 минут.
    """
    import pandas as pd
    import geopandas as gpd
    from shapely.geometry import Point

    raw = result.get("poi_details_by_iso")
    if raw is None:
        raw = result.get("pois")

    if raw is None:
        return gpd.GeoDataFrame(columns=["geometry", "minutes", "plot_category"], geometry="geometry", crs="EPSG:4326")

    if isinstance(raw, gpd.GeoDataFrame):
        df = raw.copy()
    elif isinstance(raw, pd.DataFrame):
        df = raw.copy()
    elif isinstance(raw, list):
        df = pd.DataFrame(raw)
    else:
        return gpd.GeoDataFrame(columns=["geometry", "minutes", "plot_category"], geometry="geometry", crs="EPSG:4326")

    if df.empty:
        return gpd.GeoDataFrame(columns=["geometry", "minutes", "plot_category"], geometry="geometry", crs="EPSG:4326")

    minute_col = None
    for candidate in ["Минут_пешком", "minutes", "time_min", "duration"]:
        if candidate in df.columns:
            minute_col = candidate
            break

    if minute_col is None:
        return gpd.GeoDataFrame(columns=["geometry", "minutes", "plot_category"], geometry="geometry", crs="EPSG:4326")

    df["minutes"] = pd.to_numeric(df[minute_col], errors="coerce")
    df = df[df["minutes"].isin([5, 10, 15])].copy()

    if df.empty:
        return gpd.GeoDataFrame(columns=["geometry", "minutes", "plot_category"], geometry="geometry", crs="EPSG:4326")

    df["minutes"] = df["minutes"].astype(int)

    if "geometry" not in df.columns:
        lon_col = first_column(df, ["longitude", "lon", "lng", "Долгота"])
        lat_col = first_column(df, ["latitude", "lat", "Широта"])

        if lon_col is None or lat_col is None:
            return gpd.GeoDataFrame(columns=["geometry", "minutes", "plot_category"], geometry="geometry", crs="EPSG:4326")

        df["geometry"] = [
            Point(float(lon), float(lat))
            if pd.notna(lon) and pd.notna(lat)
            else None
            for lon, lat in zip(df[lon_col], df[lat_col])
        ]

    df = df[df["geometry"].notna()].copy()

    if df.empty:
        return gpd.GeoDataFrame(columns=["geometry", "minutes", "plot_category"], geometry="geometry", crs="EPSG:4326")

    if isinstance(df, gpd.GeoDataFrame):
        gdf = df.copy()
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326", allow_override=True)
    else:
        gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

    category_col = first_column(
        gdf,
        [
            "Категория_2GIS",
            "Категория",
            "category",
            "rubric_name",
            "source_category",
            "functional_category",
        ],
    )

    if category_col is None:
        gdf["plot_category"] = "Прочие POI"
    else:
        gdf["plot_category"] = (
            gdf[category_col]
            .fillna("Прочие POI")
            .astype(str)
            .str.strip()
            .replace("", "Прочие POI")
        )

    return gdf


def first_column(df: Any, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None

