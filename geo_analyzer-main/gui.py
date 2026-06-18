from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _ensure_src_on_path() -> None:
    project_root = Path(__file__).resolve().parent
    src_dir = project_root / "src"

    if src_dir.exists():
        sys.path.insert(0, str(src_dir))


def _install_catalog_refresh_ui(app_module: Any) -> None:
    from geo_analyzer.gui import dgis_catalog_ui

    app_cls = app_module.GeoAnalyzerApp
    if getattr(app_cls, "_dgis_catalog_ui_installed", False):
        return

    original_init = app_cls.__init__
    original_begin_run = app_cls._begin_run
    original_set_buttons_running = app_cls._set_buttons_running

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        dgis_catalog_ui.attach(self, app_module.tk, app_module.ttk, app_module.CARD_BG)

    def patched_begin_run(self: Any, status: str, label: str, metrics: str) -> None:
        dgis_catalog_ui.apply_before_run(self)
        original_begin_run(self, status, label, metrics)
        dgis_catalog_ui.apply_before_run(self)

    def patched_set_buttons_running(self: Any, running: bool) -> None:
        original_set_buttons_running(self, running)
        dgis_catalog_ui.set_running(self, app_module.tk, running)

    app_cls.__init__ = patched_init
    app_cls._begin_run = patched_begin_run
    app_cls._set_buttons_running = patched_set_buttons_running
    app_cls._dgis_catalog_ui_installed = True


def main() -> None:
    _ensure_src_on_path()

    from geo_analyzer.gui import app as app_module

    _install_catalog_refresh_ui(app_module)
    app_module.main()


if __name__ == "__main__":
    main()
