from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


def export_parking_map(
    *,
    isochrones: pd.DataFrame | list[dict[str, Any]] | None,
    residential_details: pd.DataFrame | None,
    parking_details: pd.DataFrame | None,
    output_path: Path,
) -> Path | None:
    """Строит карту парковочной обеспеченности.

    Args:
        isochrones: Изохроны 5/10 минут.
        residential_details: Таблица жилых домов.
        parking_details: Таблица парковок.
        output_path: Путь к parking_map.png.

    Returns:
        Путь к файлу или None, если карта не построена.
    """
    iso_df = _as_df(isochrones)

    if iso_df.empty or "geometry" not in iso_df.columns:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 9))

    minute_col = _first_existing_column(iso_df, ["minutes", "Минут_пешком", "time_min", "duration"])

    for _, row in iso_df.iterrows():
        geometry = row.get("geometry")

        if geometry is None or not hasattr(geometry, "exterior"):
            continue

        minutes = row.get(minute_col) if minute_col else ""
        x, y = geometry.exterior.xy
        ax.plot(x, y, linewidth=1.6, label=f"Изохрона {minutes} мин")

    _scatter_details(
        ax,
        residential_details,
        label="Жилые дома",
        marker="s",
        name_col="Адрес",
    )

    for parking_type, marker in [
        ("Бесплатная", "o"),
        ("Платная", "^"),
        ("Неизвестно", "x"),
        ("Исключена из расчёта", "v"),
    ]:
        subset = _as_df(parking_details)

        if subset.empty or "Тип_парковки" not in subset.columns:
            continue

        subset = subset[subset["Тип_парковки"] == parking_type]
        _scatter_details(
            ax,
            subset,
            label=f"Парковки: {parking_type}",
            marker=marker,
            name_col="Название",
        )

    ax.set_title("Парковочная обеспеченность")
    ax.set_xlabel("Долгота")
    ax.set_ylabel("Широта")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)

    return output_path


def _scatter_details(
    ax: Any,
    df: pd.DataFrame | None,
    *,
    label: str,
    marker: str,
    name_col: str,
) -> None:
    data = _as_df(df)

    if data.empty:
        return

    lon_col = _first_existing_column(data, ["longitude", "lon", "lng", "Долгота"])
    lat_col = _first_existing_column(data, ["latitude", "lat", "Широта"])

    if lon_col and lat_col:
        x = pd.to_numeric(data[lon_col], errors="coerce")
        y = pd.to_numeric(data[lat_col], errors="coerce")
        ax.scatter(x, y, marker=marker, s=45, label=label)
        return

    if "geometry" in data.columns:
        xs = []
        ys = []

        for geometry in data["geometry"]:
            if geometry is None:
                continue

            xs.append(geometry.x)
            ys.append(geometry.y)

        if xs and ys:
            ax.scatter(xs, ys, marker=marker, s=45, label=label)


def _as_df(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()

    if isinstance(value, pd.DataFrame):
        return value.copy()

    if isinstance(value, list):
        return pd.DataFrame(value)

    return pd.DataFrame()


def _first_existing_column(df: pd.DataFrame, columns: list[str]) -> str | None:
    for column in columns:
        if column in df.columns:
            return column

    return None