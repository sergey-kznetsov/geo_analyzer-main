from __future__ import annotations

import os

CATALOG_REFRESH_ENV = "GEO_ANALYZER_REFRESH_DGIS_CATALOG"


def enabled_text(enabled: bool) -> str:
    return "1" if enabled else "0"


def set_catalog_refresh_enabled(enabled: bool) -> None:
    os.environ[CATALOG_REFRESH_ENV] = enabled_text(enabled)


__all__ = ["CATALOG_REFRESH_ENV", "enabled_text", "set_catalog_refresh_enabled"]
