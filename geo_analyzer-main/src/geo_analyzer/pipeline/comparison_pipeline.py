from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import pandas as pd

from geo_analyzer.core.models import LocationInput
from geo_analyzer.core.settings import get_settings
from geo_analyzer.pipeline.analysis_pipeline import run_analysis


ProgressCallback = Callable[..., None] | None

LOWER_IS_BETTER = {
    "Авто-время до центра",
    "Пешком до центра",
    "Антидрайверы",
    "Суммарный штраф антидрайверов",
}

KEY_SCORE_METRICS = [
    "Средняя оценка среды",
    "Инфраструктурная насыщенность",
    "Семейная пригодность",
    "Досуг и рекреация",
    "Транспортная доступность",
    "Функциональное разнообразие",
    "Проницаемость сети",
    "Энтропия функций",
]


@dataclass(slots=True)
class ComparisonResult:
    result_dir: Path
    location_a_result: dict[str, Any]
    location_b_result: dict[str, Any]
    comparison_path: Path
    summary_path: Path
    scores_chart_path: Path
    map_path: Path | None
    winner_absolute: str
    winner_benchmark: str | None
    summary_text: str


def run_location_comparison(location_a: LocationInput, location_b: LocationInput, *, progress_callback: ProgressCallback = None) -> ComparisonResult:
    settings = get_settings()
    comparison_dir = settings.output_dir / f"comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    comparison_dir.mkdir(parents=True, exist_ok=True)

    _notify(progress_callback, 1, 5, "Анализ Локации A")
    try:
        result_a = run_analysis(location_a, progress_callback=None)
    except Exception as exc:
        raise RuntimeError(f"Не удалось выполнить анализ Локации A: {exc}") from exc
    _move_result(result_a, comparison_dir / "location_a")

    _notify(progress_callback, 2, 5, "Анализ Локации B")
    try:
        result_b = run_analysis(location_b, progress_callback=None)
    except Exception as exc:
        raise RuntimeError(f"Не удалось выполнить анализ Локации B: {exc}") from exc
    _move_result(result_b, comparison_dir / "location_b")

    _notify(progress_callback, 3, 5, "Расчёт сравнения")
    metrics_a = extract_comparison_metrics(result_a)
    metrics_b = extract_comparison_metrics(result_b)
    comparison_df = build_comparison_table(metrics_a, metrics_b)
    benchmark_df = build_benchmark_comparison_table(result_a, result_b, metrics_a, metrics_b)
    summary_text, winner_absolute, winner_benchmark = build_comparison_summary(
        result_a=result_a,
        result_b=result_b,
        comparison_df=comparison_df,
        benchmark_df=benchmark_df,
    )

    _notify(progress_callback, 4, 5, "Экспорт comparison.xlsx")
    comparison_path = comparison_dir / "comparison.xlsx"
    summary_path = comparison_dir / "comparison_summary.txt"
    scores_chart_path = comparison_dir / "comparison_scores.png"
    map_path = comparison_dir / "comparison_map.png"
    export_comparison_excel(
        output_path=comparison_path,
        comparison_df=comparison_df,
        benchmark_df=benchmark_df,
        summary_text=summary_text,
        metrics_a=metrics_a,
        metrics_b=metrics_b,
    )
    summary_path.write_text(summary_text, encoding="utf-8")
    plot_comparison_scores(metrics_a, metrics_b, scores_chart_path)
    plot_comparison_map(result_a, result_b, map_path)

    meta = {
        "location_a": result_a.get("meta", {}),
        "location_b": result_b.get("meta", {}),
        "winner_absolute": winner_absolute,
        "winner_benchmark": winner_benchmark,
        "comparison_path": str(comparison_path),
        "summary_path": str(summary_path),
        "scores_chart_path": str(scores_chart_path),
        "map_path": str(map_path),
    }
    (comparison_dir / "comparison_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _notify(progress_callback, 5, 5, "Сравнение готово")
    return ComparisonResult(
        result_dir=comparison_dir,
        location_a_result=result_a,
        location_b_result=result_b,
        comparison_path=comparison_path,
        summary_path=summary_path,
        scores_chart_path=scores_chart_path,
        map_path=map_path if map_path.exists() else None,
        winner_absolute=winner_absolute,
        winner_benchmark=winner_benchmark,
        summary_text=summary_text,
    )


def extract_comparison_metrics(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    report_sheets = _load_report_sheets(result)

    quality = _first_non_empty_df(result.get("quality_scores"), report_sheets.get("Качество среды"))
    if not quality.empty:
        score_col = _first_existing_column(quality, ["Оценка_из_10", "Оценка_из_100"])
        metric_col = _first_existing_column(quality, ["Метрика", "metric"])
        if score_col and metric_col:
            normalized = quality.copy()
            normalized["_score"] = pd.to_numeric(normalized[score_col], errors="coerce")
            if score_col == "Оценка_из_100":
                normalized["_score"] = normalized["_score"] / 10
            metrics["Средняя оценка среды"] = _metric(
                "Качество среды",
                "Средняя оценка среды",
                float(normalized["_score"].dropna().mean()) if normalized["_score"].notna().any() else None,
                "балл",
            )
            aliases = {
                "Инфраструктурная насыщенность": ["инфраструктур", "насыщ"],
                "Семейная пригодность": ["семейн", "дет"],
                "Досуг и рекреация": ["досуг", "рекреац", "зел"],
                "Транспортная доступность": ["транспорт"],
                "Функциональное разнообразие": ["разнообраз"],
                "Проницаемость сети": ["проницаем", "сеть"],
                "Энтропия функций": ["энтроп"],
                "Парковочный потенциал": ["парковоч"],
            }
            for output_name, markers in aliases.items():
                value = _find_quality_metric(normalized, metric_col, markers)
                if value is not None:
                    metrics[output_name] = _metric("Качество среды", output_name, value, "балл")

    accessibility = _first_non_empty_df(result.get("accessibility_snapshot"), report_sheets.get("Доступность"))
    if not accessibility.empty:
        for minutes, label in [(5, "0-5 минут"), (10, "5-10 минут")]:
            row = _find_accessibility_row(accessibility, minutes)
            if row is None:
                continue
            metrics[f"Итоговая доступность {label}"] = _metric(
                "Доступность",
                f"Итоговая доступность {label}",
                _row_value(row, ["Итоговая_доступность_из_10", "Итоговая_доступность_из_100"], divide_100=True),
                "балл",
            )
            metrics[f"Количество POI до {minutes} минут"] = _metric(
                "Инфраструктура",
                f"Количество POI до {minutes} минут",
                _row_value(row, ["Количество_POI", "poi_count", "count"]),
                "шт.",
            )
            metrics[f"Количество категорий до {minutes} минут"] = _metric(
                "Инфраструктура",
                f"Количество категорий до {minutes} минут",
                _row_value(row, ["Количество_категорий", "category_count"]),
                "шт.",
            )
            metrics[f"Остановочные комплексы до {minutes} минут"] = _metric(
                "Транспорт",
                f"Остановочные комплексы до {minutes} минут",
                _row_value(row, ["Остановочных_комплексов", "stops_count"]),
                "шт.",
            )

        first_row = accessibility.iloc[0]
        metrics["Авто-время до центра"] = _metric(
            "Транспорт",
            "Авто-время до центра",
            _row_value(first_row, ["Авто_время_до_центра_мин", "drive_time_to_center_min"]),
            "мин",
            lower_is_better=True,
        )
        metrics["Пешком до центра"] = _metric(
            "Транспорт",
            "Пешком до центра",
            _row_value(first_row, ["Пешком_до_центра_мин", "walk_time_to_center_min"]),
            "мин",
            lower_is_better=True,
        )

    network = _first_non_empty_df(result.get("network_metrics"), report_sheets.get("Сетевые метрики"))
    network_index = _metric_by_row_name(
        network,
        ["Индекс транспортной доступности, из 10", "Индекс транспортной доступности, из 100"],
        divide_100_if_needed=True,
    )
    if network_index is not None:
        metrics["Сетевой индекс"] = _metric("Сеть", "Сетевой индекс", network_index, "балл")

    anti = _first_non_empty_df(result.get("anti_driver_summary"), report_sheets.get("Антидрайверы"))
    if anti.empty:
        metrics["Антидрайверы"] = _metric("Антидрайверы", "Антидрайверы", 0.0, "шт.", lower_is_better=True)
        metrics["Суммарный штраф антидрайверов"] = _metric(
            "Антидрайверы",
            "Суммарный штраф антидрайверов",
            0.0,
            "балл",
            lower_is_better=True,
        )
    else:
        anti_count = _sum_numeric_from_df(anti, ["Количество"])
        if anti_count is None:
            anti_count = float(len(anti))
        anti_penalty = _sum_numeric_from_df(anti, ["Суммарный_штраф", "Штраф", "penalty"])
        if anti_penalty is None:
            anti_penalty = _first_numeric_from_df(anti, ["Суммарный_штраф", "Штраф", "penalty"])
        metrics["Антидрайверы"] = _metric("Антидрайверы", "Антидрайверы", anti_count, "шт.", lower_is_better=True)
        metrics["Суммарный штраф антидрайверов"] = _metric(
            "Антидрайверы",
            "Суммарный штраф антидрайверов",
            anti_penalty if anti_penalty is not None else 0.0,
            "балл",
            lower_is_better=True,
        )
        row_5 = _find_zone_row(parking, 5, ["0–5 минут", "0-5 минут", "0–5 мин", "0-5 мин"])
        row_10 = _find_zone_row(parking, 10, ["Итого до 10 минут", "0–10 минут", "0-10 минут", "до 10 минут"])

        if row_5 is not None:
            score_5 = _parking_score_from_row(row_5)
            if score_5 is not None:
                metrics["Парковочный потенциал до 5 минут"] = _metric(
                    "Парковка",
                    score_5,
                    "балл",
                )

        if row_10 is not None:
            score_10 = _parking_score_from_row(row_10)
            if score_10 is not None:
                metrics["Парковочный потенциал"] = _metric("Парковка", "Парковочный потенциал", score_10, "балл")
                metrics["Парковочный потенциал до 10 минут"] = _metric(
                    "Парковка",
                    score_10,
                    "балл",
                )
            metrics["Квартир в зоне до 10 минут"] = _metric(
                "Парковка",
                "Квартир в зоне до 10 минут",
                _row_value(row_10, ["Квартир_в_зоне", "Квартир_всего"]),
                "шт.",
            )
            metrics["Парковочных мест до 10 минут"] = _metric(
                "Парковка",
                _row_value(row_10, ["Парковочных_мест", "Взвешенных_парковочных_мест"]),
                "шт.",
            )

    return metrics


def build_comparison_table(metrics_a: dict[str, dict[str, Any]], metrics_b: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metric_names = _ordered_metric_names(set(metrics_a) | set(metrics_b))
    for metric_name in metric_names:
        item_a = metrics_a.get(metric_name, {})
        item_b = metrics_b.get(metric_name, {})
        value_a = item_a.get("value")
        value_b = item_b.get("value")
        lower_is_better = bool(item_a.get("lower_is_better") or item_b.get("lower_is_better"))
        winner = _compare_values(value_a, value_b, lower_is_better=lower_is_better)
        difference = _difference(value_a, value_b)
        rows.append({"Метрика": metric_name, "Локация A": _display_value(value_a), "Локация B": _display_value(value_b), "Разница": difference if difference is not None else "нет данных", "Победитель": winner, "Комментарий": _comparison_comment(metric_name, value_a, value_b, winner)})
    return pd.DataFrame(rows)


def build_benchmark_comparison_table(result_a: dict[str, Any], result_b: dict[str, Any], metrics_a: dict[str, dict[str, Any]], metrics_b: dict[str, dict[str, Any]]) -> pd.DataFrame:
    meta_a = result_a.get("meta", {}) or {}
    meta_b = result_b.get("meta", {}) or {}
    city_a = meta_a.get("city")
    city_b = meta_b.get("city")
    benchmark_a = _extract_benchmark_values(result_a)
    benchmark_b = _extract_benchmark_values(result_b)
    rows: list[dict[str, Any]] = []
    for metric_name in _ordered_metric_names(set(metrics_a) | set(metrics_b)):
        value_a = metrics_a.get(metric_name, {}).get("value")
        value_b = metrics_b.get(metric_name, {}).get("value")
        bench_a = benchmark_a.get(metric_name)
        bench_b = benchmark_b.get(metric_name)
        deviation_a = _difference(value_a, bench_a)
        deviation_b = _difference(value_b, bench_b)
        lower_is_better = bool(metrics_a.get(metric_name, {}).get("lower_is_better") or metrics_b.get(metric_name, {}).get("lower_is_better"))
        absolute_winner = _compare_values(value_a, value_b, lower_is_better=lower_is_better)
        benchmark_winner = _compare_values(deviation_a, deviation_b, lower_is_better=False)
        rows.append({
            "Метрика": metric_name,
            "Город A": city_a or "нет данных",
            "Значение A": _display_value(value_a),
            "Benchmark A": _display_value(bench_a),
            "Отклонение A": _display_value(deviation_a),
            "Город B": city_b or "нет данных",
            "Значение B": _display_value(value_b),
            "Benchmark B": _display_value(bench_b),
            "Отклонение B": _display_value(deviation_b),
            "Победитель по абсолютному значению": absolute_winner,
            "Победитель относительно benchmark": benchmark_winner,
            "Комментарий": _benchmark_comment(city_a, city_b, bench_a, bench_b),
        })
    return pd.DataFrame(rows)


def export_comparison_excel(*, output_path: Path, comparison_df: pd.DataFrame, benchmark_df: pd.DataFrame, summary_text: str, metrics_a: dict[str, dict[str, Any]], metrics_b: dict[str, dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_rows = []
    for metric_name in _ordered_metric_names(set(metrics_a) | set(metrics_b)):
        item_a = metrics_a.get(metric_name, {})
        item_b = metrics_b.get(metric_name, {})
        lower_is_better = bool(item_a.get("lower_is_better") or item_b.get("lower_is_better"))
        metrics_rows.append({
            "Раздел": item_a.get("section") or item_b.get("section") or "",
            "Метрика": metric_name,
            "Значение A": _display_value(item_a.get("value")),
            "Значение B": _display_value(item_b.get("value")),
            "Единица измерения": item_a.get("unit") or item_b.get("unit") or "",
            "Тип сравнения": "меньше лучше" if lower_is_better else "больше лучше",
            "Победитель": _compare_values(item_a.get("value"), item_b.get("value"), lower_is_better=lower_is_better),
        })
    metrics_df = pd.DataFrame(metrics_rows)
    summary_df = pd.DataFrame({"Раздел": ["Саммари"], "Текст": [summary_text]})
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        comparison_df.to_excel(writer, sheet_name="Сравнение", index=False)
        metrics_df.to_excel(writer, sheet_name="Метрики A и B", index=False)
        _build_advantages_sheet(comparison_df, "Локация A").to_excel(writer, sheet_name="Преимущества A", index=False)
        _build_advantages_sheet(comparison_df, "Локация B").to_excel(writer, sheet_name="Преимущества B", index=False)
        summary_df.to_excel(writer, sheet_name="Саммари", index=False)
        benchmark_df.to_excel(writer, sheet_name="Сравнение с benchmark", index=False)


def _build_advantages_sheet(comparison_df: pd.DataFrame, winner_label: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if comparison_df.empty or "Победитель" not in comparison_df.columns:
        return pd.DataFrame(columns=["Метрика", "Значение A", "Значение B", "Разница", "Комментарий"])
    subset = comparison_df[comparison_df["Победитель"] == winner_label]
    for _, row in subset.iterrows():
        rows.append({"Метрика": row.get("Метрика", ""), "Значение A": row.get("Локация A", "нет данных"), "Значение B": row.get("Локация B", "нет данных"), "Разница": row.get("Разница", "нет данных"), "Комментарий": row.get("Комментарий", "")})
    return pd.DataFrame(rows, columns=["Метрика", "Значение A", "Значение B", "Разница", "Комментарий"])


def build_comparison_summary(*, result_a: dict[str, Any], result_b: dict[str, Any], comparison_df: pd.DataFrame, benchmark_df: pd.DataFrame) -> tuple[str, str, str | None]:
    score_a = _location_score_from_comparison(comparison_df, "Локация A")
    score_b = _location_score_from_comparison(comparison_df, "Локация B")
    winner_absolute = _winner_from_scores(score_a, score_b)
    meta_a = result_a.get("meta", {}) or {}
    meta_b = result_b.get("meta", {}) or {}
    city_a = meta_a.get("city") or "нет данных"
    city_b = meta_b.get("city") or "нет данных"
    advantages_a = comparison_df[comparison_df["Победитель"] == "Локация A"]["Метрика"].head(8).tolist()
    advantages_b = comparison_df[comparison_df["Победитель"] == "Локация B"]["Метрика"].head(8).tolist()
    benchmark_winner = None
    if city_a != city_b and not benchmark_df.empty and "Победитель относительно benchmark" in benchmark_df.columns:
        counts = benchmark_df["Победитель относительно benchmark"].value_counts().to_dict()
        benchmark_winner = _winner_from_counts(int(counts.get("Локация A", 0)), int(counts.get("Локация B", 0)))
    lines = [
        "Сравнение локаций",
        "",
        f"Локация A: {meta_a.get('resolved_address', 'нет данных')}",
        f"Локация B: {meta_b.get('resolved_address', 'нет данных')}",
        "",
        "Итог:",
        f"Локация A — {score_a:.1f} из 10" if score_a is not None else "Локация A — нет данных",
        f"Локация B — {score_b:.1f} из 10" if score_b is not None else "Локация B — нет данных",
        f"Победитель: {winner_absolute}",
        "",
    ]
    if city_a != city_b:
        lines.extend(["Локации находятся в разных городах", f"Город A: {city_a}", f"Город B: {city_b}", f"Абсолютный победитель: {winner_absolute}", f"Победитель относительно benchmark города: {benchmark_winner or 'нет данных'}", ""])
    lines.append("Локация A сильнее по:")
    lines.extend([f"- {item}" for item in advantages_a] if advantages_a else ["- нет выраженных преимуществ"])
    lines.append("")
    lines.append("Локация B сильнее по:")
    lines.extend([f"- {item}" for item in advantages_b] if advantages_b else ["- нет выраженных преимуществ"])
    lines.extend(["", "Вывод:", _build_final_conclusion(winner_absolute)])
    return "\n".join(lines), winner_absolute, benchmark_winner


def plot_comparison_scores(metrics_a: dict[str, dict[str, Any]], metrics_b: dict[str, dict[str, Any]], output_path: Path) -> None:
    labels: list[str] = []
    values_a: list[float] = []
    values_b: list[float] = []
    for metric in KEY_SCORE_METRICS:
        value_a = metrics_a.get(metric, {}).get("value")
        value_b = metrics_b.get(metric, {}).get("value")
        if value_a is None and value_b is None:
            continue
        labels.append(metric)
        values_a.append(float(value_a) if value_a is not None else 0.0)
        values_b.append(float(value_b) if value_b is not None else 0.0)
    if not labels:
        return
    x = range(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar([i - width / 2 for i in x], values_a, width, label="Локация A")
    ax.bar([i + width / 2 for i in x], values_b, width, label="Локация B")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=35, ha="right")
    ax.set_ylabel("Оценка из 10")
    ax.set_ylim(0, 10)
    ax.set_title("Сравнение качества локаций")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_comparison_map(result_a: dict[str, Any], result_b: dict[str, Any], output_path: Path) -> None:
    meta_a = result_a.get("meta", {}) or {}
    meta_b = result_b.get("meta", {}) or {}
    lat_a, lon_a = meta_a.get("latitude"), meta_a.get("longitude")
    lat_b, lon_b = meta_b.get("latitude"), meta_b.get("longitude")
    if not all(_is_number(value) for value in [lat_a, lon_a, lat_b, lon_b]):
        return
    fig, ax = plt.subplots(figsize=(9, 8))
    _plot_isochrones(ax, result_a.get("isochrones"), label_prefix="A")
    _plot_isochrones(ax, result_b.get("isochrones"), label_prefix="B")
    ax.scatter([float(lon_a)], [float(lat_a)], marker="o", s=80, label="Локация A")
    ax.scatter([float(lon_b)], [float(lat_b)], marker="s", s=80, label="Локация B")
    ax.set_title("Карта сравнения локаций")
    ax.set_xlabel("Долгота")
    ax.set_ylabel("Широта")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def _plot_isochrones(ax: Any, isochrones: Any, *, label_prefix: str) -> None:
    iso_df = _as_df(isochrones)
    if iso_df.empty or "geometry" not in iso_df.columns:
        return
    minute_col = _first_existing_column(iso_df, ["minutes", "Минут_пешком", "time_min", "duration"])
    for _, row in iso_df.iterrows():
        geometry = row.get("geometry")
        if geometry is None:
            continue
        minutes = row.get(minute_col) if minute_col else ""
        label = f"{label_prefix}: {minutes} мин"
        if hasattr(geometry, "exterior"):
            x, y = geometry.exterior.xy
            ax.plot(x, y, linewidth=1.2, label=label)


def _move_result(result: dict[str, Any], target_dir: Path) -> None:
    source = result.get("result_dir")
    if not source:
        return
    source_path = Path(source)
    if not source_path.exists():
        return
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.move(str(source_path), str(target_dir))
    result["result_dir"] = target_dir
    result.setdefault("meta", {})["result_dir"] = str(target_dir)
    report_path = target_dir / "report.xlsx"
    summary_path = target_dir / "summary.txt"
    if report_path.exists():
        result["report_path"] = report_path
        result["meta"]["report_path"] = str(report_path)
    if summary_path.exists():
        result["summary_path"] = summary_path
        result["meta"]["summary_path"] = str(summary_path)


def _extract_benchmark_values(result: dict[str, Any]) -> dict[str, float]:
    benchmark_summary = _as_df(result.get("benchmark_summary"))
    if benchmark_summary.empty:
        return {}
    metric_col = _first_existing_column(benchmark_summary, ["Метрика", "metric", "Показатель"])
    benchmark_col = _first_existing_column(benchmark_summary, ["Бенчмарк_города_из_10", "Бенчмарк_города", "Benchmark", "Бенчмарк", "Среднее_по_городу", "Городской_benchmark", "benchmark_value"])
    if not metric_col or not benchmark_col:
        return {}
    values: dict[str, float] = {}
    for _, row in benchmark_summary.iterrows():
        name = str(row.get(metric_col, "")).strip()
        value = _safe_float(row.get(benchmark_col))
        if name and value is not None:
            values[name] = value
    return values


def _load_report_sheets(result: dict[str, Any]) -> dict[str, pd.DataFrame]:
    report_path = result.get("report_path")
    if not report_path:
        result_dir = result.get("result_dir")
        if result_dir:
            candidate = Path(result_dir) / "report.xlsx"
            if candidate.exists():
                report_path = candidate
    if not report_path:
        meta = result.get("meta", {}) or {}
        candidate = meta.get("report_path")
        if candidate:
            report_path = candidate
    if not report_path:
        return {}
    try:
        report_path = Path(report_path)
        if not report_path.exists():
            return {}
        workbook = pd.ExcelFile(report_path)
        return {sheet: pd.read_excel(report_path, sheet_name=sheet) for sheet in workbook.sheet_names}
    except Exception:
        return {}


def _first_non_empty_df(*values: Any) -> pd.DataFrame:
    for value in values:
        df = _as_df(value)
        if not df.empty:
            return df
    return pd.DataFrame()


def _find_zone_row(df: pd.DataFrame, minutes: int, aliases: list[str]) -> pd.Series | None:
    if df.empty:
        return None

    minute_col = _first_existing_column(df, ["Минут_пешком", "minutes", "time_min"])
    if minute_col:
        subset = df[pd.to_numeric(df[minute_col], errors="coerce") == minutes]
        if not subset.empty:
            preferred = subset[subset.apply(lambda row: str(row.get("Зона", "")).strip() in aliases, axis=1)]
            if not preferred.empty:
                return preferred.iloc[0]
            return subset.iloc[-1]

    zone_col = _first_existing_column(df, ["Зона", "Зона_доступности", "zone"])
    if zone_col:
        normalized_aliases = {alias.strip().lower() for alias in aliases}
        subset = df[df[zone_col].astype(str).str.strip().str.lower().isin(normalized_aliases)]
        if not subset.empty:
            return subset.iloc[0]

    return None
def _find_quality_metric(df: pd.DataFrame, metric_col: str, markers: list[str]) -> float | None:
    for _, row in df.iterrows():
        name = str(row.get(metric_col, "")).lower()
        if any(marker in name for marker in markers):
            return _safe_float(row.get("_score"))
    return None


def _find_accessibility_row(df: pd.DataFrame, minutes: int) -> pd.Series | None:
    minute_col = _first_existing_column(df, ["Минут_пешком", "minutes", "time_min"])
    if minute_col:
        subset = df[pd.to_numeric(df[minute_col], errors="coerce") == minutes]
        if not subset.empty:
            return subset.iloc[0]

    zone_col = _first_existing_column(df, ["Зона_доступности", "Зона", "zone"])
    if zone_col:
        aliases = {
            5: {"0–5 мин", "0-5 мин", "0–5 минут", "0-5 минут"},
            10: {"5–10 мин", "5-10 мин", "5–10 минут", "5-10 минут", "итого до 10 минут"},
            15: {"10–15 мин", "10-15 мин", "10–15 минут", "10-15 минут"},
        }
        normalized = {value.lower() for value in aliases.get(minutes, set())}
        subset = df[df[zone_col].astype(str).str.strip().str.lower().isin(normalized)]
        if not subset.empty:
            return subset.iloc[0]

    if df.empty:
        return None
    return df.iloc[0]


def _row_value(row: pd.Series, columns: list[str], *, divide_100: bool = False) -> float | None:
    for column in columns:
        if column not in row.index:
            continue
        value = _safe_float(row.get(column))
        if value is None:
            continue
        if divide_100 and column.endswith("_из_100"):
            return value / 10
        return value
    return None


def _metric_by_row_name(df: pd.DataFrame, names: list[str], *, divide_100_if_needed: bool = False) -> float | None:
    if df.empty or not {"Метрика", "Значение"}.issubset(df.columns):
        return None
    for name in names:
        subset = df[df["Метрика"].astype(str).eq(name)]
        if subset.empty:
            continue
        value = _safe_float(subset.iloc[0].get("Значение"))
        if value is None:
            continue
        if divide_100_if_needed and ("из 100" in name.lower() or value > 10):
            return value / 10
        return value
    return None


def _first_numeric_from_df(df: pd.DataFrame, columns: list[str]) -> float | None:
    for column in columns:
        if column not in df.columns:
            continue
        values = pd.to_numeric(df[column], errors="coerce").dropna()
        if not values.empty:
            return float(values.iloc[0])
    return None


def _sum_numeric_from_df(df: pd.DataFrame, columns: list[str]) -> float | None:
    for column in columns:
        if column not in df.columns:
            continue
        values = pd.to_numeric(df[column], errors="coerce").dropna()
        if not values.empty:
            return float(values.sum())
    return None


def _compare_values(value_a: Any, value_b: Any, *, lower_is_better: bool) -> str:
    a = _safe_float(value_a)
    b = _safe_float(value_b)
    if a is None or b is None:
        return "Нет данных"
    if abs(a - b) < 0.001:
        return "Паритет"
    if lower_is_better:
        return "Локация A" if a < b else "Локация B"
    return "Локация A" if a > b else "Локация B"


def _difference(value_a: Any, value_b: Any) -> float | None:
    a = _safe_float(value_a)
    b = _safe_float(value_b)
    if a is None or b is None:
        return None
    return round(a - b, 3)


def _comparison_comment(metric: str, value_a: Any, value_b: Any, winner: str) -> str:
    if winner == "Нет данных":
        return "По одной или двум локациям нет данных."
    if winner == "Паритет":
        return "Значения близки, выраженного преимущества нет."
    return f"По метрике «{metric}» сильнее {winner}."


def _benchmark_comment(city_a: Any, city_b: Any, benchmark_a: Any, benchmark_b: Any) -> str:
    if _safe_float(benchmark_a) is None or _safe_float(benchmark_b) is None:
        return "Benchmark города недоступен."
    if city_a != city_b:
        return "Локации находятся в разных городах; сравнение выполнено по абсолютным значениям и отклонению от городского benchmark."
    return "Локации находятся в одном городе; benchmark используется как дополнительный контекст."


def _location_score_from_comparison(df: pd.DataFrame, location_label: str) -> float | None:
    if df.empty:
        return None
    winners = df[df["Победитель"].isin(["Локация A", "Локация B", "Паритет"])]
    if winners.empty:
        return None
    score = 0.0
    count = 0
    for _, row in winners.iterrows():
        if row["Победитель"] == "Паритет":
            score += 0.5
        elif row["Победитель"] == location_label:
            score += 1.0
        count += 1
    return round(score / count * 10, 2) if count else None


def _winner_from_scores(score_a: float | None, score_b: float | None) -> str:
    if score_a is None or score_b is None:
        return "Нет данных"
    if abs(score_a - score_b) < 0.1:
        return "Паритет"
    return "Локация A" if score_a > score_b else "Локация B"


def _winner_from_counts(count_a: int, count_b: int) -> str:
    if count_a == count_b:
        return "Паритет"
    return "Локация A" if count_a > count_b else "Локация B"


def _build_final_conclusion(winner: str) -> str:
    if winner == "Локация A":
        return "Локация A выглядит сильнее по совокупности сравниваемых метрик."
    if winner == "Локация B":
        return "Локация B выглядит сильнее по совокупности сравниваемых метрик."
    return "По совокупности метрик выраженного победителя нет."


def _metric(section: str, name: str, value: float | None, unit: str, *, lower_is_better: bool = False) -> dict[str, Any]:
    return {"section": section, "name": name, "value": value, "unit": unit, "lower_is_better": lower_is_better}


def _display_value(value: Any) -> Any:
    numeric = _safe_float(value)
    if numeric is None:
        return "нет данных"
    return round(numeric, 3)


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


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_number(value: Any) -> bool:
    return _safe_float(value) is not None


def _ordered_metric_names(names: set[str]) -> list[str]:
    preferred = KEY_SCORE_METRICS + ["Авто-время до центра", "Пешком до центра", "Антидрайверы", "Суммарный штраф антидрайверов", "Сетевой индекс"]
    ordered = [name for name in preferred if name in names]
    ordered.extend(sorted(name for name in names if name not in ordered))
    return ordered


def _notify(progress_callback: ProgressCallback, step: int, total: int, message: str) -> None:
    if progress_callback is None:
        return
    for variant in [
        lambda: progress_callback(step, total, message),
        lambda: progress_callback(step, message),
        lambda: progress_callback(message),
        lambda: progress_callback({"step": step, "total": total, "message": message}),
    ]:
        try:
            variant()
            return
        except TypeError:
            continue
        except Exception:
            return
