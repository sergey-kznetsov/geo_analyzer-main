from __future__ import annotations

from typing import Any

import pandas as pd

from .diversity import shannon_entropy


EDUCATION_FUNCTION = "Образование и развитие"
MEDICINE_FUNCTION = "Медицина и здоровье"
DAILY_FUNCTION = "Повседневная торговля и услуги"
SPORT_FUNCTION = "Спорт и активный отдых"
LEISURE_FUNCTION = "Досуг и городское притяжение"
SECONDARY_FUNCTION = "Вторичные и фоновые услуги"
TRANSPORT_FUNCTION = "Транспорт"
OTHER_FUNCTION = "Прочее"


def _score_0_10(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 2)))


def _empty_scores() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "Метрика",
            "Оценка_из_10",
            "Пояснение",
            "Шкала_оценки",
        ]
    )


def _safe_numeric(series: pd.Series | Any) -> pd.Series:
    if isinstance(series, pd.Series):
        return pd.to_numeric(series, errors="coerce").fillna(0)
    return pd.Series([series]).pipe(pd.to_numeric, errors="coerce").fillna(0)


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


def _prepare_counts(poi_counts_by_iso: pd.DataFrame | None) -> pd.DataFrame:
    if poi_counts_by_iso is None or poi_counts_by_iso.empty:
        return pd.DataFrame()

    data = poi_counts_by_iso.copy()

    required_defaults = {
        "Минут_пешком": 15,
        "Количество": 0,
        "functional_category": OTHER_FUNCTION,
        "criticality_score": 0,
        "Категория": "Прочее",
    }

    for column, default in required_defaults.items():
        if column not in data.columns:
            data[column] = default

    data["Минут_пешком"] = _safe_numeric(data["Минут_пешком"]).astype(int)
    data["Количество"] = _safe_numeric(data["Количество"])
    data["criticality_score"] = _safe_numeric(data["criticality_score"])
    data["functional_category"] = data["functional_category"].fillna(OTHER_FUNCTION).astype(str)

    return data


def _zone_coef(minutes: int, target: str) -> float:
    if target == "daily":
        if minutes <= 5:
            return 1.00
        if minutes <= 10:
            return 0.70
        return 0.20

    if target == "education":
        if minutes <= 5:
            return 1.00
        if minutes <= 10:
            return 0.75
        return 0.35

    if target == "medicine":
        if minutes <= 5:
            return 0.85
        if minutes <= 10:
            return 0.75
        return 0.55

    if target == "leisure":
        if minutes <= 5:
            return 0.85
        if minutes <= 10:
            return 1.00
        return 0.70

    if target == "transport":
        if minutes <= 5:
            return 1.00
        if minutes <= 10:
            return 0.65
        return 0.25

    if target == "secondary":
        if minutes <= 5:
            return 0.25
        if minutes <= 10:
            return 0.20
        return 0.10

    return 0.50


def _weighted_score(
    data: pd.DataFrame,
    functions: set[str],
    target: str,
    normalization_target: float,
) -> float:
    if data.empty:
        return 0.0

    subset = data[data["functional_category"].isin(functions)].copy()

    if subset.empty:
        return 0.0

    subset["zone_coef"] = subset["Минут_пешком"].apply(lambda value: _zone_coef(int(value), target))
    subset["weighted_value"] = subset["Количество"] * subset["criticality_score"] * subset["zone_coef"]

    raw_value = float(subset["weighted_value"].sum())

    if normalization_target <= 0:
        return 0.0

    return _score_0_10(raw_value / normalization_target * 10)


def _mean_snapshot_score(
    accessibility_snapshot: pd.DataFrame | None,
    columns_10: list[str],
    columns_100: list[str] | None = None,
) -> float | None:
    if accessibility_snapshot is None or accessibility_snapshot.empty:
        return None

    for column in columns_10:
        if column in accessibility_snapshot.columns:
            values = _safe_numeric(accessibility_snapshot[column])
            if not values.empty:
                return _score_0_10(float(values.mean()))

    for column in columns_100 or []:
        if column in accessibility_snapshot.columns:
            values = _safe_numeric(accessibility_snapshot[column])
            if not values.empty:
                return _score_0_10(float(values.mean()) / 10)

    return None


def _metric_value_by_name(metrics: pd.DataFrame | None, markers: list[str], value_columns: list[str] | None = None) -> float | None:
    if metrics is None or metrics.empty:
        return None
    metric_col = next((c for c in ["Метрика", "metric", "Показатель", "name"] if c in metrics.columns), None)
    if metric_col is None:
        return None
    value_cols = value_columns or ["Значение", "Оценка_из_10", "score", "value", "Оценка_из_100"]
    prepared = metrics.copy()
    prepared["_metric_name"] = prepared[metric_col].astype(str).str.replace("ё", "е").str.lower()
    for marker in markers:
        marker_norm = str(marker).replace("ё", "е").lower()
        subset = prepared[prepared["_metric_name"].str.contains(marker_norm, regex=False, na=False)]
        if subset.empty:
            continue
        for value_col in value_cols:
            if value_col not in subset.columns:
                continue
            value = _safe_float(subset.iloc[0].get(value_col))
            if value is None:
                continue
            if value_col == "Оценка_из_100" or value > 10 and value <= 100:
                value = value / 10
            return _score_0_10(value)
    return None


def _transport_score(
    accessibility_snapshot: pd.DataFrame | None,
    poi_counts: pd.DataFrame,
    network_metrics: pd.DataFrame | None = None,
) -> float:
    """Считает транспортную доступность в той же логике, что и вкладка «Сетевые метрики».

    Главный источник — строка «Индекс транспортной доступности, из 10» из network_metrics.
    Это убирает расхождение, когда в саммари было 6.54, а в сетевых метриках 8.49.
    Если строки нет, используется старый fallback по остановкам и авто-доступности.
    """
    network_score = _metric_value_by_name(
        network_metrics,
        ["индекс транспортной доступности", "транспортной доступности"],
        value_columns=["Значение", "Оценка_из_10", "score", "value", "Оценка_из_100"],
    )
    if network_score is not None:
        return network_score

    stop_snapshot_score = _mean_snapshot_score(
        accessibility_snapshot,
        columns_10=["Остановочная_доступность_из_10"],
        columns_100=["Остановочная_доступность_из_100"],
    )

    auto_snapshot_score = _mean_snapshot_score(
        accessibility_snapshot,
        columns_10=["Авто_доступность_до_центра_из_10"],
        columns_100=["Авто_доступность_до_центра_из_100"],
    )

    old_transport_score = _mean_snapshot_score(
        accessibility_snapshot,
        columns_10=["Транспортная_доступность_из_10"],
        columns_100=["Транспортная_доступность_из_100"],
    )

    stops_weighted_score = _weighted_score(
        data=poi_counts,
        functions={TRANSPORT_FUNCTION},
        target="transport",
        normalization_target=35,
    )

    if stop_snapshot_score is None:
        stop_snapshot_score = stops_weighted_score

    if auto_snapshot_score is None and old_transport_score is not None:
        return _score_0_10(old_transport_score * 0.70 + stops_weighted_score * 0.30)

    if auto_snapshot_score is None:
        auto_snapshot_score = 0.0

    return _score_0_10(stop_snapshot_score * 0.65 + auto_snapshot_score * 0.35)


def _category_diversity_score(category_summary: pd.DataFrame | None) -> float:
    if category_summary is None or category_summary.empty:
        return 0.0

    if "Категория" not in category_summary.columns:
        return 0.0

    categories_count = int(category_summary["Категория"].dropna().nunique())

    return _score_0_10(categories_count / 12 * 10)


def _entropy_score(category_summary: pd.DataFrame | None) -> float:
    if category_summary is None or category_summary.empty:
        return 0.0

    if not {"Категория", "Количество"}.issubset(category_summary.columns):
        return 0.0

    entropy_input = category_summary.rename(columns={"Категория": "category", "Количество": "count"})
    entropy_raw = float(shannon_entropy(entropy_input))

    if entropy_raw <= 0:
        return 0.0

    return _score_0_10((entropy_raw / 2.3) * 10)


def _anti_driver_penalty_0_10(anti_driver_penalty: int | float) -> float:
    return max(0.0, min(3.0, float(anti_driver_penalty) / 12))


def build_quality_scores(
    poi_counts_by_iso: pd.DataFrame | None,
    category_summary: pd.DataFrame | None,
    network_metrics: pd.DataFrame | None,
    anti_driver_penalty: int = 0,
    accessibility_snapshot: pd.DataFrame | None = None,
    parking_supply_summary: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Считает итоговые оценки качества среды по шкале 0–10."""
    poi_counts = _prepare_counts(poi_counts_by_iso)

    if poi_counts.empty:
        return _empty_scores()

    daily_score = _weighted_score(
        data=poi_counts,
        functions={DAILY_FUNCTION},
        target="daily",
        normalization_target=95,
    )

    education_score = _weighted_score(
        data=poi_counts,
        functions={EDUCATION_FUNCTION},
        target="education",
        normalization_target=75,
    )

    medicine_score = _weighted_score(
        data=poi_counts,
        functions={MEDICINE_FUNCTION},
        target="medicine",
        normalization_target=55,
    )

    sport_score = _weighted_score(
        data=poi_counts,
        functions={SPORT_FUNCTION},
        target="leisure",
        normalization_target=35,
    )

    leisure_score = _weighted_score(
        data=poi_counts,
        functions={LEISURE_FUNCTION},
        target="leisure",
        normalization_target=85,
    )

    secondary_score = _weighted_score(
        data=poi_counts,
        functions={SECONDARY_FUNCTION},
        target="secondary",
        normalization_target=50,
    )

    transport_score = _transport_score(accessibility_snapshot, poi_counts, network_metrics=network_metrics)
    diversity_score = _category_diversity_score(category_summary)
    entropy_score = _entropy_score(category_summary)
    anti_penalty = _anti_driver_penalty_0_10(anti_driver_penalty)

    infra_score = _score_0_10(
        daily_score * 0.45
        + medicine_score * 0.18
        + education_score * 0.12
        + sport_score * 0.08
        + secondary_score * 0.04
        + diversity_score * 0.13
        - anti_penalty * 0.20
    )

    family_score = _score_0_10(
        education_score * 0.42
        + daily_score * 0.22
        + medicine_score * 0.16
        + transport_score * 0.14
        + leisure_score * 0.06
        - anti_penalty * 0.25
    )

    recreation_score = _score_0_10(
        leisure_score * 0.50
        + sport_score * 0.18
        + daily_score * 0.10
        + transport_score * 0.10
        + diversity_score * 0.12
        - anti_penalty * 0.15
    )

    mixed_use_score = _score_0_10(
        diversity_score * 0.38
        + entropy_score * 0.30
        + daily_score * 0.16
        + leisure_score * 0.10
        + medicine_score * 0.06
    )

    permeability_score = _score_0_10(
        transport_score * 0.48
        + daily_score * 0.16
        + leisure_score * 0.12
        + diversity_score * 0.14
        + entropy_score * 0.10
        - anti_penalty * 0.10
    )

    entropy_final = _score_0_10(entropy_score - anti_penalty)

    rows = [
        {
            "Метрика": "Инфраструктурная насыщенность",
            "Оценка_из_10": infra_score,
            "Пояснение": (
                "Показывает обеспеченность локации базовыми функциями. "
                "Объекты в ближайших изохронах дают больший вклад, "
                "объекты в 10–15 минутах получают понижающий коэффициент."
            ),
            "Шкала_оценки": "0–3 низкая насыщенность, 3–6 средняя, 6–8 хорошая, 8–10 сильная.",
        },
        {
            "Метрика": "Семейная пригодность",
            "Оценка_из_10": family_score,
            "Пояснение": (
                "Оценивает семейный сценарий через образование, медицину, "
                "повседневную торговлю, транспорт и рекреацию. "
                "Школы и детские сады в ближних зонах имеют больший вес."
            ),
            "Шкала_оценки": "0–3 слабый семейный сценарий, 3–6 средний, 6–8 хороший, 8–10 сильный.",
        },
        {
            "Метрика": "Досуг и рекреация",
            "Оценка_из_10": recreation_score,
            "Пояснение": (
                "Показывает силу прогулочного, спортивного и досугового сценария. "
                "Городские точки притяжения могут давать вклад в любой изохроне, "
                "но ближние зоны оцениваются выше."
            ),
            "Шкала_оценки": "0–3 слабая среда, 3–6 средняя, 6–8 хорошая, 8–10 сильная рекреация.",
        },
        {
            "Метрика": "Транспортная доступность",
            "Оценка_из_10": transport_score,
            "Пояснение": (
                "Сводная оценка транспортной доступности. "
                "Значение синхронизировано со строкой «Индекс транспортной доступности, из 10» "
                "во вкладке сетевых метрик."
            ),
            "Шкала_оценки": "0–3 слабая, 3–6 средняя, 6–8 хорошая, 8–10 сильная доступность.",
        },
        {
            "Метрика": "Функциональное разнообразие",
            "Оценка_из_10": mixed_use_score,
            "Пояснение": (
                "Показывает, насколько среда смешанная и сценарно насыщенная. "
                "Чем больше разных категорий 2GIS представлены сбалансированно, тем выше оценка."
            ),
            "Шкала_оценки": "0–3 монофункциональная среда, 3–6 средняя, 6–8 разнообразная, 8–10 сильная mixed-use среда.",
        },
        {
            "Метрика": "Проницаемость сети",
            "Оценка_из_10": permeability_score,
            "Пояснение": (
                "Прокси-оценка связности локации с окружающей средой. "
                "Учитывает транспорт, разнообразие функций, насыщенность ближних зон и штрафы среды."
            ),
            "Шкала_оценки": "0–3 слабая связность, 3–6 средняя, 6–8 хорошая, 8–10 сильная.",
        },
        {
            "Метрика": "Энтропия функций",
            "Оценка_из_10": entropy_final,
            "Пояснение": (
                "Показывает равномерность распределения категорий. "
                "Если среда перекошена в один тип объектов, оценка снижается."
            ),
            "Шкала_оценки": "0–3 среда однотипная, 3–6 умеренно разнообразная, 6–8 разнообразная, 8–10 максимально сбалансированная.",
        },
    ]
    return pd.DataFrame(rows)
