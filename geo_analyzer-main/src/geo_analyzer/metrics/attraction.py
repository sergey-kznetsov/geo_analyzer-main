from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


CITY_SCALE_ALLOWED_KEYWORDS = {
    "парк", "сквер", "бульвар", "набережная", "лесопарк", "парк отдыха", "парк культуры", "парк развлечений", "аттракцион",
    "торговый центр", "торговые центры", "торгово-развлекательный центр", "торгово-развлекательные центры",
    "торгово-развлекательный комплекс", "торгово-развлекательные комплексы", "торговый комплекс", "торговые комплексы",
    "тц", "трц", "трк", "молл", "mall",
    "театр", "музей", "кинотеатр", "стадион", "спортивная арена", "арена", "выставочный центр", "филармония", "цирк", "зоопарк", "аквапарк", "концертный зал", "дом культуры", "дк",
    "детские игровые залы", "детский игровой зал", "детский развлекательный центр", "детский развлекательный комплекс", "семейный парк развлечений", "батутный центр", "центр развлечений", "развлекательный центр", "игровой центр для детей",
}

SHOPPING_MALL_KEYWORDS = {
    "торговый центр", "торговые центры", "торгово-развлекательный центр", "торгово-развлекательные центры",
    "торгово-развлекательный комплекс", "торгово-развлекательные комплексы", "торговый комплекс", "торговые комплексы",
    "тц", "трц", "трк", "молл", "mall",
}

SUPPORTING_LEISURE_KEYWORDS = {"кафе", "ресторан", "кофейня", "фитнес", "спорт", "тренажерный зал", "бассейн"}

BLOCKED_KEYWORDS = {
    "парикмахер", "салон красоты", "ногт", "барбершоп", "косметолог", "визаж", "магазин", "продукты", "аптека", "пункт выдачи", "интернет-заказ", "ремонт", "ателье", "химчист", "банк", "офис", "склад", "сервис", "автомат", "игровые автоматы", "зал игровых автоматов", "букмекер", "казино", "лотерея", "вендинг", "vending",
}

CITY_SCALE_CATEGORY_WEIGHT = 1.00
SUPPORTING_CATEGORY_WEIGHT = 0.55
BACKGROUND_CATEGORY_WEIGHT = 0.00


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value).replace("ё", "е").strip().lower())


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, (list, tuple, set)):
        return " ".join(_normalize(item) for item in value if _normalize(item))
    return _normalize(value)


def _row_text(row: pd.Series) -> str:
    values: list[str] = []
    for column in ["Название", "name", "Адрес", "Категория", "Категория_2GIS", "functional_category", "source_category_2gis", "source_categories_2gis", "rubrics_2gis", "category_groups_2gis", "rubric_id"]:
        if column in row.index:
            text = _as_text(row.get(column))
            if text:
                values.append(text)
    return " ".join(values)


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _safe_rating(value: Any) -> float:
    try:
        if value is None or pd.isna(value) or value == "":
            return 0.0
        return max(0.0, min(5.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _safe_reviews(value: Any) -> int:
    try:
        if value is None or pd.isna(value):
            return 0
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _safe_minutes(value: Any) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _distance_weight(minutes: int | None) -> float:
    if minutes is None:
        return 0.35
    if minutes <= 5:
        return 1.00
    if minutes <= 10:
        return 0.82
    if minutes <= 15:
        return 0.62
    return 0.15


def _rating_weight(rating: float) -> float:
    if rating <= 0:
        return 0.76
    return 0.76 + (rating / 5.0) * 0.24


def _reviews_weight(reviews_count: int) -> float:
    if reviews_count <= 0:
        return 0.78
    return min(1.18, 0.78 + math.log1p(reviews_count) / 12)


def _is_shopping_mall(row: pd.Series) -> bool:
    text = _row_text(row)
    if not text:
        return False
    if _contains_any(text, SHOPPING_MALL_KEYWORDS):
        return True
    return re.search(r"(?<![а-яa-z0-9])(тц|трц|трк)(?![а-яa-z0-9])", text) is not None


def _is_blocked(row: pd.Series) -> bool:
    if _is_shopping_mall(row):
        return False
    return _contains_any(_row_text(row), BLOCKED_KEYWORDS)


def _is_city_scale(row: pd.Series) -> bool:
    text = _row_text(row)
    if not text:
        return False
    if _is_shopping_mall(row):
        return True
    if _is_blocked(row):
        return False
    return _contains_any(text, CITY_SCALE_ALLOWED_KEYWORDS)


def _is_supporting_attraction(row: pd.Series) -> bool:
    text = _row_text(row)
    if not text or _is_blocked(row):
        return False
    return _contains_any(text, SUPPORTING_LEISURE_KEYWORDS)


def _category_weight(row: pd.Series) -> float:
    if _is_city_scale(row):
        return CITY_SCALE_CATEGORY_WEIGHT
    if _is_supporting_attraction(row):
        return SUPPORTING_CATEGORY_WEIGHT
    return BACKGROUND_CATEGORY_WEIGHT


def _classify_attraction(score_10: float, is_city_scale: bool, is_supporting: bool) -> str:
    if is_city_scale and score_10 >= 6.0:
        return "Городская точка притяжения"
    if is_city_scale:
        return "Городской объект слабого притяжения"
    if is_supporting and score_10 >= 5.2:
        return "Поддерживающий объект"
    return "Фоновый объект"


def _build_attraction_points_table(poi_details_by_iso: pd.DataFrame | None) -> pd.DataFrame:
    output_columns = ["Название", "Адрес", "Категория_2GIS", "Функциональная_группа", "Минут_пешком", "Зона_доступности", "Городской_масштаб", "Балл_притяжения_из_10", "Статус_объекта"]
    if poi_details_by_iso is None or poi_details_by_iso.empty:
        return pd.DataFrame(columns=output_columns)
    df = poi_details_by_iso.copy()
    defaults = {"Название": "", "Адрес": "", "Категория_2GIS": "Прочее", "Категория": "Прочее", "functional_category": "Прочее", "Минут_пешком": pd.NA, "Зона_доступности": pd.NA, "Рейтинг": 0, "Количество_отзывов": 0}
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default
    if "Категория_2GIS" not in df.columns or df["Категория_2GIS"].isna().all():
        df["Категория_2GIS"] = df["Категория"]
    df["Функциональная_группа"] = df["functional_category"]
    df["_rating"] = df["Рейтинг"].apply(_safe_rating)
    df["_reviews"] = df["Количество_отзывов"].apply(_safe_reviews)
    df["_city_scale"] = df.apply(_is_city_scale, axis=1)
    df["_supporting"] = df.apply(_is_supporting_attraction, axis=1)
    df["_category_weight"] = df.apply(_category_weight, axis=1)
    df["_distance_weight"] = df["Минут_пешком"].apply(lambda value: _distance_weight(_safe_minutes(value)))
    df["_rating_weight"] = df["_rating"].apply(_rating_weight)
    df["_reviews_weight"] = df["_reviews"].apply(_reviews_weight)
    df["Балл_притяжения_из_10"] = (df["_category_weight"] * df["_distance_weight"] * df["_rating_weight"] * df["_reviews_weight"] * 10).clip(0, 10).round(2)
    df["Городской_масштаб"] = df["_city_scale"].map(lambda value: "Да" if bool(value) else "Нет")
    df["Статус_объекта"] = df.apply(lambda row: _classify_attraction(float(row["Балл_притяжения_из_10"]), bool(row["_city_scale"]), bool(row["_supporting"])), axis=1)
    result = df[df["Статус_объекта"].isin(["Городская точка притяжения", "Городской объект слабого притяжения", "Поддерживающий объект"])].copy()
    if result.empty:
        return pd.DataFrame(columns=output_columns)
    return result.sort_values(["Балл_притяжения_из_10", "Минут_пешком", "Название"], ascending=[False, True, True], na_position="last").reset_index(drop=True)[output_columns]


def _metric_value(df: pd.DataFrame | None, metric_names: list[str]) -> float:
    if df is None or df.empty or not {"Метрика", "Значение"}.issubset(df.columns):
        return 0.0
    for metric_name in metric_names:
        row = df.loc[df["Метрика"].astype(str).eq(metric_name), "Значение"]
        if not row.empty:
            try:
                return float(row.iloc[0])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def compute_attraction_score(category_summary: pd.DataFrame, network_metrics: pd.DataFrame, temporal_snapshot: pd.DataFrame | None = None, poi_details_by_iso: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    del temporal_snapshot
    attraction_points = _build_attraction_points_table(poi_details_by_iso)
    total_poi = int(category_summary["Количество"].sum()) if category_summary is not None and not category_summary.empty and "Количество" in category_summary.columns else 0
    categories_count = int(category_summary["Категория_2GIS"].nunique()) if category_summary is not None and not category_summary.empty and "Категория_2GIS" in category_summary.columns else int(category_summary["Категория"].nunique()) if category_summary is not None and not category_summary.empty and "Категория" in category_summary.columns else 0
    transport_access_score_10 = _metric_value(network_metrics, ["Индекс транспортной доступности, из 10"])
    if transport_access_score_10 <= 0:
        transport_access_score_10 = _metric_value(network_metrics, ["Индекс транспортной доступности, из 100"]) / 10
    density_score_10 = _metric_value(network_metrics, ["Плотность POI в 10 минутах, из 10"])
    city_points = attraction_points[attraction_points["Статус_объекта"] == "Городская точка притяжения"]
    weak_city_points = attraction_points[attraction_points["Статус_объекта"] == "Городской объект слабого притяжения"]
    supporting_points = attraction_points[attraction_points["Статус_объекта"] == "Поддерживающий объект"]
    points_score_10 = float(attraction_points.head(20)["Балл_притяжения_из_10"].mean()) if not attraction_points.empty else 0.0
    city_points_score_10 = float(city_points.head(10)["Балл_притяжения_из_10"].mean()) if not city_points.empty else 0.0
    ten_min_city_points = int(len(city_points[pd.to_numeric(city_points["Минут_пешком"], errors="coerce") <= 10])) if not city_points.empty else 0
    city_bonus_10 = min(2.2, ten_min_city_points * 1.1)
    raw_attraction_score = points_score_10 * 2.8 + city_points_score_10 * 2.9 + transport_access_score_10 * 1.2 + density_score_10 * 0.9 + min(categories_count / 20 * 10, 10) * 0.9 + city_bonus_10
    attraction_score = round(min(100.0, raw_attraction_score), 2)
    explanation = "Индекс притяжения показывает наличие рядом объектов, создающих внешний поток людей: парки, скверы, ТЦ/ТРЦ/ТРК, театры, музеи, кинотеатры, спортивные арены, детские развлекательные центры и другие места массового досуга. Обычные сервисы, ПВЗ, парикмахерские, салоны и игровые автоматы исключаются."
    summary = pd.DataFrame([
        {"Показатель": "Всего POI", "Значение": total_poi, "Пояснение": explanation, "Шкала_оценки": "Абсолютное значение."},
        {"Показатель": "Количество категорий 2GIS", "Значение": categories_count, "Пояснение": explanation, "Шкала_оценки": "Чем больше категорий, тем выше сценарное разнообразие."},
        {"Показатель": "Городских точек притяжения", "Значение": int(len(city_points)), "Пояснение": explanation, "Шкала_оценки": "Ключевой плюс локации."},
        {"Показатель": "Городских точек притяжения до 10 минут", "Значение": ten_min_city_points, "Пояснение": explanation, "Шкала_оценки": "Если есть в 10 минутах — сильный плюс."},
        {"Показатель": "Городских объектов слабого притяжения", "Значение": int(len(weak_city_points)), "Пояснение": explanation, "Шкала_оценки": "Городские объекты с меньшей силой притяжения."},
        {"Показатель": "Поддерживающих объектов", "Значение": int(len(supporting_points)), "Пояснение": explanation, "Шкала_оценки": "Кафе, рестораны, спорт и другие объекты фоновой активности."},
        {"Показатель": "Средний балл сильнейших объектов, из 10", "Значение": round(points_score_10, 2), "Пояснение": explanation, "Шкала_оценки": "0-10."},
        {"Показатель": "Бонус городского притяжения", "Значение": round(city_bonus_10, 2), "Пояснение": explanation, "Шкала_оценки": "0-10."},
        {"Показатель": "Индекс притяжения, из 100", "Значение": attraction_score, "Пояснение": explanation, "Шкала_оценки": "0-35 слабый, 36-60 средний, 61-80 хороший, 81-100 сильный."},
    ])
    return summary, attraction_points
