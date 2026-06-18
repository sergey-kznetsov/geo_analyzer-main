from __future__ import annotations

from geo_analyzer.gui import dgis_catalog_ui


class _Var:
    def __init__(self, value: bool) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value


class _App:
    pass


def test_catalog_refresh_env_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv(dgis_catalog_ui.REFRESH_ENV, raising=False)

    assert dgis_catalog_ui.is_enabled() is False


def test_catalog_refresh_env_can_be_enabled(monkeypatch):
    monkeypatch.delenv(dgis_catalog_ui.REFRESH_ENV, raising=False)

    dgis_catalog_ui.set_enabled(True)

    assert dgis_catalog_ui.is_enabled() is True
    assert dgis_catalog_ui.os.environ[dgis_catalog_ui.REFRESH_ENV] == "1"


def test_apply_before_run_reads_gui_checkbox(monkeypatch):
    monkeypatch.delenv(dgis_catalog_ui.REFRESH_ENV, raising=False)
    app = _App()
    app.refresh_dgis_catalog_var = _Var(True)

    dgis_catalog_ui.apply_before_run(app)

    assert dgis_catalog_ui.is_enabled() is True
