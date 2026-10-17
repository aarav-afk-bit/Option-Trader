import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep tests from touching the real ~/.option_trader directory."""
    from option_trader import config

    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv("OPTION_TRADER_DATA_DIR", str(tmp_path / "data"))
    yield
