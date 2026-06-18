from __future__ import annotations

from pathlib import Path

import pandas as pd

from geo_analyzer.core.models import ResolvedLocation


def _safe_score(row: pd.Series) -> float:
    """Поддерживает шкалу 0–10 и старую шкалу 0–100."""
    if "Оценка_из_10" in row.index and pd.notna(row["Оценка_из_10"]):
        return max(0.0, min(10.0, float(row["Оценка_из_10"])))

    if "Оценка_из_100" in row.index and pd.notna(row["Оценка_из_100"]):
        return max(0.0, min(10.0, float(row["Оценка_из_100"]) / 10))

    return 0.0


def _top_categories_text(category_summary: pd.DataFrame | None, limit: int = 5) -> str:
    """Формирует текст по ключевым категориям 2GIS."""
    if category_summary is None or category_summary.empty:
        return "выраженные категории не определены"

    data = category_summary.copy()

    if "Категория_2GIS" not in data.columns:
        data["Категория_2GIS"] = data.get("Категория", "Прочее")

    if "Количество" not in data.columns:
        data["Количество"] = 0

    sort_columns = ["Количество"]
    ascending = [False]

    if "Суммарный_вес_критичности" in data.columns:
        sort_columns = ["Суммарный_вес_критичности", "Количество"]
        ascending = [False, False]

    top = data.sort_values(sort_columns, ascending=ascending).head(limit)

    parts: list[str] = []

    for _, row in top.iterrows():
        category = str(row.get("Категория_2GIS", "Прочее")).strip()
        count = int(pd.to_numeric(pd.Series([row.get("Количество", 0)]), errors="coerce").fillna(0).iloc[0])
        parts.append(f"{category} — {count} объектов")

    return ", ".join(parts) if parts else "выраженные категории не определены"


def _top_quality_metrics_text(quality_scores: pd.DataFrame | None, limit: int = 3) -> str:
    """Формирует текст по сильным сторонам среды."""
    if quality_scores is None or quality_scores.empty:
        return "сильные стороны не определены"

    data = quality_scores.copy()
    data["score"] = data.apply(_safe_score, axis=1)

    top = data.sort_values("score", ascending=False).head(limit)

    parts: list[str] = []

    for _, row in top.iterrows():
        metric = str(row.get("Метрика", "")).strip()
        score = round(float(row["score"]), 1)
        parts.append(f"{metric} — {score} из 10")

    return ", ".join(parts) if parts else "сильные стороны не определены"


def _weak_quality_metrics_text(quality_scores: pd.DataFrame | None, limit: int = 2) -> str:
    """Формирует текст по слабым сторонам среды."""
    if quality_scores is None or quality_scores.empty:
        return "выраженные слабые стороны не определены"

    data = quality_scores.copy()
    data["score"] = data.apply(_safe_score, axis=1)

    bottom = data.sort_values("score", ascending=True).head(limit)

    parts: list[str] = []

    for _, row in bottom.iterrows():
        metric = str(row.get("Метрика", "")).strip()
        score = round(float(row["score"]), 1)
        parts.append(f"{metric} — {score} из 10")

    return ", ".join(parts) if parts else "выраженные слабые стороны не определены"


def _top_attraction_points_text(attraction_points: pd.DataFrame | None, limit: int = 5) -> str:
    """Формирует описание ключевых точек притяжения."""
    if attraction_points is None or attraction_points.empty:
        return "выраженные точки притяжения не выявлены"

    data = attraction_points.copy()

    if "Статус_объекта" in data.columns:
        priority = data[
            data["Статус_объекта"].astype(str).isin(
                [
                    "Городская точка притяжения",
                    "Городской объект слабого притяжения",
                    "Поддерживающий объект",
                ]
            )
        ].copy()

        if not priority.empty:
            data = priority

    if "Балл_притяжения_из_100" in data.columns:
        data = data.sort_values("Балл_притяжения_из_100", ascending=False)

    top = data.head(limit)

    parts: list[str] = []

    for _, row in top.iterrows():
        name = str(row.get("Название") or "без названия").strip()
        category = str(row.get("Категория_2GIS") or row.get("Категория") or "").strip()
        minutes = row.get("Минут_пешком")

        label = name

        if category:
            label += f" ({category})"

        if pd.notna(minutes):
            label += f", {int(float(minutes))} мин"

        parts.append(label)

    return "; ".join(parts) if parts else "выраженные точки притяжения не выявлены"


def _anti_drivers_text(anti_driver_summary: pd.DataFrame | None) -> str:
    """Формирует текст по антидрайверам."""
    if anti_driver_summary is None or anti_driver_summary.empty:
        return "существенные антидрайверы не выявлены"

    parts: list[str] = []

    for _, row in anti_driver_summary.head(5).iterrows():
        label = str(row.get("Тип_антидрайвера", "")).strip()
        count = int(pd.to_numeric(pd.Series([row.get("Количество", 0)]), errors="coerce").fillna(0).iloc[0])

        if label:
            parts.append(f"{label} — {count}")

    return "; ".join(parts) if parts else "существенные антидрайверы не выявлены"


def _accessibility_score_10(row: pd.Series) -> float:
    """Возвращает итоговую доступность в шкале 0–10."""
    if "Итоговая_доступность_из_10" in row.index and pd.notna(row["Итоговая_доступность_из_10"]):
        return max(0.0, min(10.0, float(row["Итоговая_доступность_из_10"])))

    if "Итоговая_доступность_из_100" in row.index and pd.notna(row["Итоговая_доступность_из_100"]):
        return max(0.0, min(10.0, float(row["Итоговая_доступность_из_100"]) / 10))

    return 0.0


def _transport_text(accessibility_snapshot: pd.DataFrame | None) -> str:
    """Формирует текст транспортного профиля."""
    if accessibility_snapshot is None or accessibility_snapshot.empty:
        return "транспортный профиль не рассчитан"

    data = accessibility_snapshot.copy()
    data["score_10"] = data.apply(_accessibility_score_10, axis=1)

    row = data.sort_values("score_10", ascending=False).iloc[0]

    score_10 = round(float(row.get("score_10", 0)), 1)
    zone = str(row.get("Зона_доступности", "") or "").strip()

    if not zone and pd.notna(row.get("Минут_пешком")):
        minutes = int(float(row.get("Минут_пешком")))
        if minutes <= 5:
            zone = "0–5 мин"
        elif minutes <= 10:
            zone = "5–10 мин"
        else:
            zone = "10–15 мин"

    stops = int(pd.to_numeric(pd.Series([row.get("Остановочных_комплексов", 0)]), errors="coerce").fillna(0).iloc[0])
    categories = int(pd.to_numeric(pd.Series([row.get("Количество_категорий", 0)]), errors="coerce").fillna(0).iloc[0])
    poi = int(pd.to_numeric(pd.Series([row.get("Количество_POI", 0)]), errors="coerce").fillna(0).iloc[0])

    return (
        f"лучшая зона доступности — {zone or 'не определена'}, "
        f"итоговая оценка — {score_10} из 10, "
        f"{stops} остановочных комплексов, "
        f"{categories} категорий 2GIS и "
        f"{poi} объектов внутри зоны"
    )


def build_text_summary(
    location: ResolvedLocation,
    category_summary: pd.DataFrame | None,
    quality_scores: pd.DataFrame | None,
    anti_driver_summary: pd.DataFrame | None,
    attraction_points: pd.DataFrame | None = None,
    accessibility_snapshot: pd.DataFrame | None = None,
) -> str:
    """Строит текстовый саммари-отчёт по всей аналитике."""
    total_poi = (
        int(pd.to_numeric(category_summary["Количество"], errors="coerce").fillna(0).sum())
        if category_summary is not None and not category_summary.empty and "Количество" in category_summary.columns
        else 0
    )

    avg_score = (
        round(float(quality_scores.apply(_safe_score, axis=1).mean()), 1)
        if quality_scores is not None and not quality_scores.empty
        else 0.0
    )

    top_categories = _top_categories_text(category_summary)
    strongest_metrics = _top_quality_metrics_text(quality_scores)
    weakest_metrics = _weak_quality_metrics_text(quality_scores)
    attraction_text = _top_attraction_points_text(attraction_points)
    anti_info = _anti_drivers_text(anti_driver_summary)
    transport_info = _transport_text(accessibility_snapshot)

    if avg_score >= 8:
        overall_grade = "сильная"
    elif avg_score >= 6:
        overall_grade = "хорошая"
    elif avg_score >= 4:
        overall_grade = "средняя"
    else:
        overall_grade = "слабая"

    if avg_score >= 8:
        interpretation = "локация поддерживает насыщенный городской сценарий и подходит для mixed-use и жилых форматов высокого класса"
    elif avg_score >= 6:
        interpretation = "локация имеет хороший городской потенциал, но требует настройки продукта под сильные стороны среды"
    elif avg_score >= 4:
        interpretation = "локация требует осторожной интерпретации и проверки сценариев использования через продуктовую модель"
    else:
        interpretation = "локация обладает ограниченной городской насыщенностью и требует дополнительной проверки гипотез"

    return (
        f"Точка анализа: {location.source_label}. "
        f"Координаты: {location.latitude}, {location.longitude}. "
        f"В зоне анализа найдено {total_poi} объектов инфраструктуры. "
        f"Средняя интегральная оценка среды — {avg_score} из 10, "
        f"что позволяет охарактеризовать локацию как {overall_grade}. "
        f"Наиболее выраженные категории 2GIS: {top_categories}. "
        f"Сильные стороны локации: {strongest_metrics}. "
        f"Слабые стороны: {weakest_metrics}. "
        f"Транспортный профиль: {transport_info}. "
        f"Ключевые точки притяжения: {attraction_text}. "
        f"Антидрайверы: {anti_info}. "
        f"Итоговая интерпретация: {interpretation}."
    )


def export_text_summary(text: str, output_path: Path) -> None:
    """Экспортирует текстовое summary в txt."""
    output_path.write_text(text, encoding="utf-8")