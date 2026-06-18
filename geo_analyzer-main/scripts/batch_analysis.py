from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from geo_analyzer.pipeline.batch_pipeline import run_batch_analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Пакетный запуск Geo Analyzer")
    parser.add_argument("--input", required=True, help="Путь к CSV/XLSX файлу со списком адресов или координат")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_batch_analysis(args.input)


if __name__ == "__main__":
    raise SystemExit(main())