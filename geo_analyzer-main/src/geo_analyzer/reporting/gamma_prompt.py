from __future__ import annotations

import pandas as pd


def _status_contains(df: pd.DataFrame, text: str) -> pd.Series:
    if "Статус_объекта" not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    return df["Статус_объекта"].astype(str).str.contains(text, case=False, na=False)


def build_gamma_prompt(analysis_result: dict) -> str:
    meta = analysis_result["meta"]
    summary = analysis_result["category_summary"]
    scores = analysis_result["quality_scores"]
    poi_by_iso = analysis_result["poi_details_by_iso"]
    benchmark = analysis_result["benchmark_summary"]
    attraction_summary = analysis_result["attraction_summary"]
    attraction_points = analysis_result.get("attraction_points")

    top_categories = ""
    if not summary.empty:
        top_rows = summary.head(7)
        top_categories = "\n".join(
            [
                f"- {row['Категория']}: {int(row['Количество'])} объектов ({row['Доля_проц']}%), примеры: {row['Примеры_объектов']}"
                for _, row in top_rows.iterrows()
            ]
        )

    score_rows = ""
    if not scores.empty:
        score_rows = "\n".join(
            [f"- {row['Метрика']}: {int(row['Оценка_из_100'])} из 100" for _, row in scores.iterrows()]
        )

    benchmark_rows = ""
    if not benchmark.empty:
        benchmark_rows = "\n".join(
            [
                f"- {row['Метрика']}: район — {row['Бенчмарк_района']}, город — {row['Бенчмарк_города']}"
                for _, row in benchmark.iterrows()
            ]
        )

    iso_rows = ""
    if not poi_by_iso.empty:
        sort_columns = [col for col in ["Минут_пешком", "Категория", "Название"] if col in poi_by_iso.columns]
        prepared = poi_by_iso.sort_values(sort_columns) if sort_columns else poi_by_iso

        label_col = "Зона_доступности" if "Зона_доступности" in prepared.columns else "Минут_пешком"
        for label, data in prepared.groupby(label_col, sort=False):
            if label_col == "Минут_пешком":
                title = f"до {int(label)} минут пешком"
            else:
                title = f"{label} пешком"

            zone_data = data.head(8)
            iso_rows += f"\nЗона {title}:\n"
            iso_rows += "\n".join(
                [f"- {row['Название']} / {row['Категория']} / {row['Адрес']}" for _, row in zone_data.iterrows()]
            )
            iso_rows += "\n"

    attraction_rows = ""
    if attraction_summary is not None and not attraction_summary.empty:
        attraction_rows = "\n".join(
            [
                f"- {row['Показатель']}: {row['Значение']}"
                for _, row in attraction_summary.iterrows()
            ]
        )

    attraction_points_rows = ""
    if attraction_points is not None and not attraction_points.empty:
        strong_points = attraction_points[_status_contains(attraction_points, "притяж")].head(10)
        if strong_points.empty:
            strong_points = attraction_points[~_status_contains(attraction_points, "фонов")].head(10)
        if strong_points.empty:
            strong_points = attraction_points.head(10)

        attraction_points_rows = "\n".join(
            [
                f"- {row['Название']} / {row['Категория']} / {row['Минут_пешком']} мин / балл {round(float(row['Балл_притяжения_из_100']), 1)}"
                for _, row in strong_points.iterrows()
            ]
        )

    prompt = f"""
Сделай деловую презентацию на русском языке по гео-анализу локации.

Структура: 9 слайдов.
1. Титульный слайд.
2. Локация и контекст.
3. Изохроны 0–5 / 5–10 / 10–15 минут с пояснением.
4. Что доступно в 0–5 / 5–10 / 10–15 минутах пешком.
5. Категориальная структура инфраструктуры.
6. Индексы качества и бенчмарки района / города.
7. Точки притяжения и сила локации.
8. Выводы и продуктовые последствия.
9. Контактный слайд.

Исходные данные:
Адрес / метка: {meta['resolved_address']}
Координаты: {meta['latitude']}, {meta['longitude']}
Радиус POI: {meta['poi_radius_m']} м
Изохроны: 0–5 / 5–10 / 10–15 минут
Провайдер транспортного слоя: {meta.get('provider', 'n/a')}

Краткий вывод:
{analysis_result['text_summary']}

Топ категорий инфраструктуры:
{top_categories}

Индексы качества:
{score_rows}

Бенчмарки:
{benchmark_rows}

Содержимое по изохронам:
{iso_rows}

Сводка по притяжению:
{attraction_rows}

Главные точки притяжения:
{attraction_points_rows}

Требования к стилю:
Тон деловой, понятный, без сложной теории.
На каждом аналитическом слайде должен быть короткий вывод.
Не просто перечисляй цифры, а объясняй, что они значат для девелопера, маркетинга и продукта.
Особенно выдели:
- какие объекты реально тянут людей
- какие сценарии жизни поддерживает локация
- где локация сильна, а где у неё ограничения
"""
    return prompt.strip()
