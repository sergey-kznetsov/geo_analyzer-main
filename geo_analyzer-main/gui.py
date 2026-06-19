from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from geo_analyzer.core.settings import get_settings

# Загружаем .env до импорта GUI, чтобы карточка статуса API видела ключ.
get_settings()

from geo_analyzer.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())