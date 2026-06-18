from __future__ import annotations

"""Anti-driver scoring based only on already loaded 2GIS POI.

This module does not run additional text ``q`` searches against 2GIS. The
program-wide data contract is API-only through structured loading: POI are
loaded by resolved 2GIS rubric IDs in ``places_loader`` and this module only
classifies that already loaded dataset.
"""

import re
from typing import Any

import pandas as pd

from geo_analyzer.core.logger import get_logger


logger = get_logger("geo_analyzer.metrics.anti_drivers")

ANTI_DRIVER_LOADER_VERSION = "anti_driver_poi_only_v3"

ANTI_DRIVER_RULES = [
    {
        "name": "Промышленность",
        "keywords": {
            "завод",
            "производство",
            "цех",
            "промзона",
            "промбаза",
            "складской комплекс",
            "логистический центр",
            "индустриальный парк",
        },
        "severity": 10,
        "group": "Промышленность",
    },
    {
        "name": "Железная дорога",
        "keywords": {
            "железная дорога",
            "железнодорожные пути",
            "железнодорожная станция",
            "ж/д станция",
            "ж/д вокзал",
            "сортировочная станция",
            "железнодорожный переезд",
            "железнодорожное депо",
        },
        "severity": 9,
        "group": "Шум и транспорт",
    },
    {
        "name": "Трамвайные пути",
        "keywords": {
            "трамвайные пути",
            "трамвайная линия",
            "трамвайное депо",
            "трамвайный парк",
            "трамвайное кольцо",
        },
        "severity": 6,
        "group": "Шум и транспорт",
    },
    {
        "name": "Шумная магистраль",
        "keywords": {
            "магистраль",
            "автомагистраль",
            "шоссе",
            "транспортная развязка",
            "развязка",
            "эстакада",
            "мостовая развязка",
            "кольцевая дорога",
            "федеральная трасса",
        },
        "severity": 7,
        "group": "Транспорт и шум",
    },
    {
        "name": "АЗС",
        "keywords": {
            "азс",
            "автозаправка",
            "газпромнефть",
            "лукойл",
            "татнефть",
            "роснефть",
            "bashneft",
        },
        "severity": 8,
        "group": "Экология и транспорт",
    },
    {
        "name": "Гаражи и автостоянки",
        "keywords": {
            "гаражный кооператив",
            "гск",
            "гаражный комплекс",
            "автостоянка",
            "открытая автостоянка",
        },
        "severity": 5,
        "group": "Транспорт и визуальный шум",
    },
    {
        "name": "Кладбище",
        "keywords": {"кладбище", "крематорий"},
        "severity": 10,
        "group": "Негативное соседство",
    },
    {
        "name": "Свалка и мусор",
        "keywords": {
            "полигон тбо",
            "свалка",
            "мусоросортировочный",
            "утилизация отходов",
            "переработка отходов",
        },
        "severity": 10,
        "group": "Экология",
    },
    {
        "name": "ТЭЦ и энергетика",
        "keywords": {
            "тэц",
            "котельная",
            "подстанция",
            "электроподстанция",
            "электростанция",
        },
        "severity": 9,
        "group": "Экология и шум",
    },
]

EXCLUDED_KEYWORDS = {
    "остановка",
    "автобусная остановка",
    "трамвайная остановка",
    "троллейбусная остановка",
    "станция метро",
    "метро",
    "парк",
    "сквер",
    "школа",
    "детский сад",
    "кафе",
    "ресторан",
    "кофейня",
    "торговый центр",
    "тц",
    "трц",
}

RAIL_EXCLUSION_KEYWORDS = {
    "трамвай",
    "трамвайная остановка",
    "трамвайные пути",
    "трамвайная линия",
}


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value).replace("ё", "е").strip().lower())


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _build_text(row: pd.Series) -> str:
    """Build classifier text without address-derived street names."""
    values: list[str] = []
    for column in [
        "Название",
        "Категория",
        "Категория_2GIS",
        "Категория_2GIS_официальная",
        "source_category_2gis",
        "source_categories_2gis",
        "rubrics_2gis",
        "category_groups_2gis",
        "branch_type_2gis",
        "functional_category",
    ]:
        if column not in row.index:
            continue
        for item in _as_list(row.get(column)):
            text = _normalize(item)
            if text:
                values.append(text)
    return " ".join(values)


def _is_excluded(text: str, rule_name: str | None = None) -> bool:
    if not text:
        return True
    if rule_name == "Железная дорога" and any(keyword in text for keyword in RAIL_EXCLUSION_KEYWORDS):
        return True
    return any(keyword in text for keyword in EXCLUDED_KEYWORDS)


def _empty_anti_driver_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "Название",
            "Адрес",
            "Категория_2GIS",
            "Тип_антидрайвера",
            "Группа_антидрайвера",
            "Штраф",
            "Минут_пешком",
            "Зона_доступности",
        ]
    )


def detect_anti_drivers(
    pois: pd.DataFrame | None,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_m: int | None = None,
) -> pd.DataFrame:
    """Detect anti-drivers in the already loaded 2GIS POI table.

    The coordinate arguments are accepted for API compatibility with the old
    function signature. They are not used for extra API calls.
    """
    del latitude, longitude, radius_m

    if pois is None or pois.empty:
        return _empty_anti_driver_frame()

    rows: list[dict[str, Any]] = []

    for _, row in pois.copy().iterrows():
        text = _build_text(row)
        if not text:
            continue

        for rule in ANTI_DRIVER_RULES:
            rule_name = str(rule["name"])
            if _is_excluded(text, rule_name=rule_name):
                continue
            if any(keyword in text for keyword in rule["keywords"]):
                rows.append(
                    {
                        "Название": row.get("Название"),
                        "Адрес": row.get("Адрес"),
                        "Категория_2GIS": row.get("Категория_2GIS") or row.get("Категория"),
                        "Тип_антидрайвера": rule_name,
                        "Группа_антидрайвера": rule["group"],
                        "Штраф": rule["severity"],
                        "Минут_пешком": row.get("Минут_пешком"),
                        "Зона_доступности": row.get("Зона_доступности"),
                    }
                )
                break

    if not rows:
        return _empty_anti_driver_frame()

    result = pd.DataFrame(rows)
    result["Штраф"] = pd.to_numeric(result["Штраф"], errors="coerce").fillna(0).astype(int)
    result = result.drop_duplicates(subset=["Название", "Адрес", "Тип_антидрайвера"], keep="first")

    columns = list(_empty_anti_driver_frame().columns)
    return (
        result.sort_values(["Штраф", "Минут_пешком", "Название"], ascending=[False, True, True], na_position="last")
        .reset_index(drop=True)[columns]
    )


def build_anti_driver_summary(anti_drivers: pd.DataFrame | None) -> pd.DataFrame:
    columns = [
        "Тип_антидрайвера",
        "Группа_антидрайвера",
        "Количество",
        "Суммарный_штраф",
        "Средний_штраф",
        "Пояснение",
    ]
    if anti_drivers is None or anti_drivers.empty:
        return pd.DataFrame(columns=columns)

    result = (
        anti_drivers.groupby(["Тип_антидрайвера", "Группа_антидрайвера"], dropna=False)
        .agg(
            Количество=("Тип_антидрайвера", "size"),
            Суммарный_штраф=("Штраф", "sum"),
            Средний_штраф=("Штраф", "mean"),
        )
        .reset_index()
    )
    result["Средний_штраф"] = pd.to_numeric(result["Средний_штраф"], errors="coerce").fillna(0).round(2)
    result["Пояснение"] = (
        "Антидрайверы определяются только по уже загруженным структурным POI 2GIS. "
        "Дополнительный текстовый q-search в 2GIS не используется."
    )
    return result.sort_values(["Суммарный_штраф", "Количество"], ascending=[False, False]).reset_index(drop=True)[columns]


def calculate_anti_driver_penalty(anti_driver_summary: pd.DataFrame | None) -> float:
    if anti_driver_summary is None or anti_driver_summary.empty:
        return 0.0
    total_penalty = float(pd.to_numeric(anti_driver_summary["Суммарный_штраф"], errors="coerce").fillna(0).sum())
    return round(min(total_penalty / 25, 10), 2)
