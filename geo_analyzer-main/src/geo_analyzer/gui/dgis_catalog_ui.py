from __future__ import annotations

import os
from typing import Any

REFRESH_ENV = "GEO_ANALYZER_REFRESH_DGIS_CATALOG"


def is_enabled() -> bool:
    return str(os.getenv(REFRESH_ENV, "0")).strip().lower() in {"1", "true", "yes", "y", "да", "on"}


def set_enabled(value: bool) -> None:
    os.environ[REFRESH_ENV] = "1" if value else "0"


def attach(app: Any, tk: Any, ttk: Any, card_bg: str) -> None:
    try:
        root = app.winfo_children()[0]
        input_card = root.grid_slaves(row=1, column=0)[0]
    except Exception:
        return

    app.refresh_dgis_catalog_var = tk.BooleanVar(value=is_enabled())
    app.refresh_dgis_catalog_check = ttk.Checkbutton(
        input_card,
        text="Обновить каталог 2GIS",
        variable=app.refresh_dgis_catalog_var,
    )
    app.refresh_dgis_catalog_check.grid(row=4, column=1, sticky="w", padx=(0, 8), pady=(0, 16))
    app.refresh_dgis_catalog_hint = ttk.Label(
        input_card,
        text="Скачивает официальный каталог и профиль полей для региона текущей локации. Обычный запуск каталог не обновляет.",
        style="Muted.TLabel",
    )
    app.refresh_dgis_catalog_hint.grid(row=4, column=2, columnspan=3, sticky="w", padx=(0, 18), pady=(0, 16))


def apply_before_run(app: Any) -> None:
    value = False
    try:
        value = bool(app.refresh_dgis_catalog_var.get())
    except Exception:
        value = False
    set_enabled(value)


def set_running(app: Any, tk: Any, running: bool) -> None:
    try:
        app.refresh_dgis_catalog_check.configure(state=tk.DISABLED if running else tk.NORMAL)
    except Exception:
        pass


__all__ = ["attach", "apply_before_run", "is_enabled", "set_enabled", "set_running"]
