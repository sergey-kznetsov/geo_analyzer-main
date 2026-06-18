from __future__ import annotations

from pathlib import Path

import pandas as pd

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.models import LocationInput
from geo_analyzer.pipeline.analysis_pipeline import run_analysis

logger = get_logger("geo_analyzer.pipeline.batch")


def _read_input_table(input_path: Path) -> pd.DataFrame:
    suffix = input_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(input_path)
    if suffix == ".csv":
        return pd.read_csv(input_path)
    raise ValueError("Поддерживаются только CSV и Excel-файлы.")


def run_batch_analysis(input_path: str | None = None) -> int:
    if not input_path:
        logger.error("Для batch-анализа нужно передать путь к CSV/XLSX файлу.")
        return 1

    table_path = Path(input_path)
    if not table_path.exists():
        logger.error("Файл не найден: %s", table_path)
        return 1

    df = _read_input_table(table_path)
    if df.empty:
        logger.error("Входной файл пустой: %s", table_path)
        return 1

    if "address" not in df.columns and not {"latitude", "longitude"}.issubset(df.columns):
        logger.error("Во входном файле должна быть колонка address либо latitude + longitude.")
        return 1

    success_count = 0
    failed_count = 0

    for index, row in df.iterrows():
        try:
            if pd.notna(row.get("address")) and str(row.get("address")).strip():
                location_input = LocationInput(address=str(row["address"]).strip())
            else:
                location_input = LocationInput(
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                )

            logger.info("Запуск batch-анализа для строки %s", index + 1)
            run_analysis(location_input)
            success_count += 1
        except Exception as exc:
            failed_count += 1
            logger.exception("Ошибка batch-анализа в строке %s: %s", index + 1, exc)

    logger.info("Batch-анализ завершён. Успешно: %s, ошибок: %s", success_count, failed_count)
    return 0 if failed_count == 0 else 2