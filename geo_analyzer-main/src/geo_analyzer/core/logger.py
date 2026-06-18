from __future__ import annotations

import logging
import logging.config
from pathlib import Path
from typing import Any

import yaml


def setup_logging(config_path: Path) -> None:
    """Настраивает logging из YAML-конфига.

    Если файл отсутствует, пустой или битый, приложение не падает,
    а использует базовое логирование.
    """
    if not config_path.exists():
        logging.basicConfig(level=logging.INFO)
        return

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            config: Any = yaml.safe_load(fh)

        if isinstance(config, dict) and config:
            logging.config.dictConfig(config)
        else:
            logging.basicConfig(level=logging.INFO)

    except Exception:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).warning("Не удалось прочитать logging config: %s", config_path)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)