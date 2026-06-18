from pathlib import Path

from geo_analyzer.gui import app


def test_env_path_is_inside_app_root(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(app, "_app_root", lambda: tmp_path)

    assert app._env_path() == tmp_path / ".env"


def test_write_and_read_env_value(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(app, "_app_root", lambda: tmp_path)
    monkeypatch.delenv("DGIS_API_KEY", raising=False)

    app._write_env_value("DGIS_API_KEY", "test-key")

    assert (tmp_path / ".env").exists()
    assert app._read_env_value("DGIS_API_KEY") == "test-key"