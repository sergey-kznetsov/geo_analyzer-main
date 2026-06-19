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
LOWER_IS_BETTER = {"Авто-время до центра", "Пешком до центра", "Антидрайверы", "Суммарный штраф антидрайверов"}
KEY_SCORE_METRICS = ["Средняя оценка среды", "Инфраструктурная насыщенность", "Семейная пригодность", "Досуг и рекреация", "Транспортная доступность", "Функциональное разнообразие", "Проницаемость сети", "Энтропия функций"]


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
    result_dir = settings.output_dir / f"comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    result_dir.mkdir(parents=True, exist_ok=True)
    _notify(progress_callback, 1, 5, "Анализ Локации A")
    result_a = run_analysis(location_a, progress_callback=None)
    _move_result(result_a, result_dir / "location_a")
    _notify(progress_callback, 2, 5, "Анализ Локации B")
    result_b = run_analysis(location_b, progress_callback=None)
    _move_result(result_b, result_dir / "location_b")
    _notify(progress_callback, 3, 5, "Расчёт сравнения")
    metrics_a = extract_comparison_metrics(result_a)
    metrics_b = extract_comparison_metrics(result_b)
    comparison_df = build_comparison_table(metrics_a, metrics_b)
    summary_text, winner = build_comparison_summary(result_a, result_b, comparison_df)
    comparison_path = result_dir / "comparison.xlsx"
    summary_path = result_dir / "comparison_summary.txt"
    chart_path = result_dir / "comparison_scores.png"
    map_path = result_dir / "comparison_map.png"
    _notify(progress_callback, 4, 5, "Экспорт comparison.xlsx")
    export_comparison_excel(comparison_path, comparison_df, summary_text, metrics_a, metrics_b)
    summary_path.write_text(summary_text, encoding="utf-8")
    plot_comparison_scores(metrics_a, metrics_b, chart_path)
    plot_comparison_map(result_a, result_b, map_path)
    (result_dir / "comparison_meta.json").write_text(json.dumps({"location_a": result_a.get("meta", {}), "location_b": result_b.get("meta", {}), "winner_absolute": winner, "comparison_path": str(comparison_path), "summary_path": str(summary_path)}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _notify(progress_callback, 5, 5, "Сравнение готово")
    return ComparisonResult(result_dir, result_a, result_b, comparison_path, summary_path, chart_path, map_path if map_path.exists() else None, winner, None, summary_text)


def extract_comparison_metrics(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    quality = _df(result.get("quality_scores"))
    if not quality.empty and {"Метрика", "Оценка_из_10"}.issubset(quality.columns):
        q = quality.copy()
        q["_score"] = pd.to_numeric(q["Оценка_из_10"], errors="coerce")
        scores = q["_score"].dropna()
        metrics["Средняя оценка среды"] = _metric("Качество среды", "Средняя оценка среды", float(scores.mean()) if not scores.empty else None, "балл")
        aliases = {"Инфраструктурная насыщенность": ["инфраструктур", "насыщ"], "Семейная пригодность": ["семейн", "дет"], "Досуг и рекреация": ["досуг", "рекреац"], "Транспортная доступность": ["транспорт"], "Функциональное разнообразие": ["разнообраз"], "Проницаемость сети": ["проницаем", "сеть"], "Энтропия функций": ["энтроп"]}
        for out, markers in aliases.items():
            value = _find_metric(q, markers)
            if value is not None:
                metrics[out] = _metric("Качество среды", out, value, "балл")
    access = _df(result.get("accessibility_snapshot"))
    if not access.empty:
        for minutes, label in [(5, "0-5 минут"), (10, "5-10 минут"), (15, "10-15 минут")]:
            row = _find_access_row(access, minutes)
            if row is not None:
                metrics[f"Итоговая доступность {label}"] = _metric("Доступность", f"Итоговая доступность {label}", _row(row, ["Итоговая_доступность_из_10", "Итоговая_доступность_из_100"], True), "балл")
                metrics[f"Количество POI до {minutes} минут"] = _metric("Инфраструктура", f"Количество POI до {minutes} минут", _row(row, ["Количество_POI", "poi_count", "count"]), "шт.")
        row0 = access.iloc[0]
        metrics["Авто-время до центра"] = _metric("Транспорт", "Авто-время до центра", _row(row0, ["Авто_время_до_центра_мин", "drive_time_to_center_min"]), "мин", lower_is_better=True)
        metrics["Пешком до центра"] = _metric("Транспорт", "Пешком до центра", _row(row0, ["Пешком_до_центра_мин", "walk_time_to_center_min"]), "мин", lower_is_better=True)
    anti = _df(result.get("anti_driver_summary"))
    if anti.empty:
        metrics["Антидрайверы"] = _metric("Антидрайверы", "Антидрайверы", 0, "шт.", lower_is_better=True)
        metrics["Суммарный штраф антидрайверов"] = _metric("Антидрайверы", "Суммарный штраф антидрайверов", 0, "балл", lower_is_better=True)
    else:
        metrics["Антидрайверы"] = _metric("Антидрайверы", "Антидрайверы", _sum(anti, ["Количество"]) or len(anti), "шт.", lower_is_better=True)
        metrics["Суммарный штраф антидрайверов"] = _metric("Антидрайверы", "Суммарный штраф антидрайверов", _sum(anti, ["Суммарный_штраф", "Штраф", "penalty"]) or 0, "балл", lower_is_better=True)
    return metrics


def build_comparison_table(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for name in _ordered(set(a) | set(b)):
        va, vb = a.get(name, {}).get("value"), b.get(name, {}).get("value")
        lower = bool(a.get(name, {}).get("lower_is_better") or b.get(name, {}).get("lower_is_better"))
        winner = _compare(va, vb, lower)
        rows.append({"Метрика": name, "Локация A": _display(va), "Локация B": _display(vb), "Разница": _display(_diff(va, vb)), "Победитель": winner, "Комментарий": _comment(name, winner)})
    return pd.DataFrame(rows)


def export_comparison_excel(path: Path, df: pd.DataFrame, summary: str, a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = pd.DataFrame([{"Раздел": (a.get(k, {}) or b.get(k, {})).get("section", ""), "Метрика": k, "Значение A": _display(a.get(k, {}).get("value")), "Значение B": _display(b.get(k, {}).get("value")), "Единица": (a.get(k, {}) or b.get(k, {})).get("unit", "")} for k in _ordered(set(a) | set(b))])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Сравнение", index=False)
        metrics.to_excel(writer, sheet_name="Метрики A и B", index=False)
        pd.DataFrame({"Текст": [summary]}).to_excel(writer, sheet_name="Саммари", index=False)


def build_comparison_summary(a: dict[str, Any], b: dict[str, Any], df: pd.DataFrame) -> tuple[str, str]:
    score_a = _score_from_winners(df, "Локация A")
    score_b = _score_from_winners(df, "Локация B")
    winner = _winner(score_a, score_b)
    ma, mb = a.get("meta", {}) or {}, b.get("meta", {}) or {}
    lines = ["Сравнение локаций", "", f"Локация A: {ma.get('resolved_address', 'нет данных')}", f"Локация B: {mb.get('resolved_address', 'нет данных')}", "", f"Локация A — {score_a:.1f} из 10" if score_a is not None else "Локация A — нет данных", f"Локация B — {score_b:.1f} из 10" if score_b is not None else "Локация B — нет данных", f"Победитель: {winner}"]
    return "\n".join(lines), winner


def plot_comparison_scores(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]], path: Path) -> None:
    labels, va, vb = [], [], []
    for key in KEY_SCORE_METRICS:
        if key in a or key in b:
            labels.append(key); va.append(float(a.get(key, {}).get("value") or 0)); vb.append(float(b.get(key, {}).get("value") or 0))
    if not labels:
        return
    x = range(len(labels)); width = 0.35
    fig, ax = plt.subplots(figsize=(12, 6)); ax.bar([i - width / 2 for i in x], va, width, label="Локация A"); ax.bar([i + width / 2 for i in x], vb, width, label="Локация B")
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, rotation=35, ha="right"); ax.set_ylim(0, 10); ax.legend(); fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def plot_comparison_map(a: dict[str, Any], b: dict[str, Any], path: Path) -> None:
    ma, mb = a.get("meta", {}) or {}, b.get("meta", {}) or {}
    lat_a, lon_a, lat_b, lon_b = map(_float, [ma.get("latitude"), ma.get("longitude"), mb.get("latitude"), mb.get("longitude")])
    if None in {lat_a, lon_a, lat_b, lon_b}: return
    fig, ax = plt.subplots(figsize=(8, 7)); ax.scatter([lon_a, lon_b], [lat_a, lat_b], s=120); ax.text(lon_a, lat_a, " A"); ax.text(lon_b, lat_b, " B"); ax.plot([lon_a, lon_b], [lat_a, lat_b], linestyle="--"); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def _notify(cb: ProgressCallback, step: int, total: int, msg: str) -> None:
    if cb:
        try: cb(step, total, msg)
        except TypeError: cb(msg)


def _move_result(result: dict[str, Any], target: Path) -> None:
    src = Path(result.get("result_dir", "")) if result.get("result_dir") else None
    if src and src.exists():
        if target.exists(): shutil.rmtree(target)
        shutil.move(str(src), str(target)); result["result_dir"] = target


def _metric(section: str, name: str, value: Any, unit: str, *, lower_is_better: bool = False) -> dict[str, Any]:
    return {"section": section, "name": name, "value": _float(value), "unit": unit, "lower_is_better": lower_is_better or name in LOWER_IS_BETTER}


def _df(v: Any) -> pd.DataFrame:
    if isinstance(v, pd.DataFrame): return v.copy()
    if isinstance(v, list): return pd.DataFrame(v)
    if isinstance(v, dict): return pd.DataFrame([v])
    return pd.DataFrame()


def _float(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v): return None
        return float(v)
    except Exception: return None


def _find_metric(df: pd.DataFrame, markers: list[str]) -> float | None:
    for _, r in df.iterrows():
        name = str(r.get("Метрика", "")).replace("ё", "е").lower()
        if any(m.replace("ё", "е") in name for m in markers): return _float(r.get("_score"))
    return None


def _find_access_row(df: pd.DataFrame, minutes: int) -> pd.Series | None:
    if "Минут_пешком" in df.columns:
        s = df[pd.to_numeric(df["Минут_пешком"], errors="coerce") == minutes]
        if not s.empty: return s.iloc[0]
    return None


def _row(row: pd.Series, cols: list[str], div100: bool = False) -> float | None:
    for c in cols:
        if c in row.index:
            v = _float(row.get(c))
            if v is not None: return v / 10 if div100 and (c.endswith("_из_100") or v > 10) else v
    return None


def _sum(df: pd.DataFrame, cols: list[str]) -> float | None:
    for c in cols:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce").dropna()
            if not s.empty: return float(s.sum())
    return None


def _compare(a: Any, b: Any, lower: bool) -> str:
    fa, fb = _float(a), _float(b)
    if fa is None or fb is None: return "Нет данных"
    if abs(fa - fb) < .001: return "Паритет"
    return "Локация A" if (fa < fb if lower else fa > fb) else "Локация B"


def _diff(a: Any, b: Any) -> float | None:
    fa, fb = _float(a), _float(b)
    return None if fa is None or fb is None else round(fa - fb, 3)


def _display(v: Any) -> Any:
    f = _float(v)
    return "нет данных" if f is None else round(f, 3)


def _ordered(names: set[str]) -> list[str]:
    order = {n: i for i, n in enumerate(KEY_SCORE_METRICS)}
    return sorted(names, key=lambda n: (order.get(n, 999), n))


def _comment(name: str, winner: str) -> str:
    if winner == "Нет данных": return "По одной или двум локациям нет данных."
    if winner == "Паритет": return "Значения близки."
    return f"По метрике «{name}» сильнее {winner}."


def _score_from_winners(df: pd.DataFrame, label: str) -> float | None:
    if df.empty or "Победитель" not in df.columns: return None
    rows = df[df["Победитель"].isin(["Локация A", "Локация B", "Паритет"])]
    if rows.empty: return None
    score = sum(.5 if r["Победитель"] == "Паритет" else 1 if r["Победитель"] == label else 0 for _, r in rows.iterrows())
    return round(score / len(rows) * 10, 2)


def _winner(a: float | None, b: float | None) -> str:
    if a is None or b is None: return "Нет данных"
    if abs(a - b) < .001: return "Паритет"
    return "Локация A" if a > b else "Локация B"
