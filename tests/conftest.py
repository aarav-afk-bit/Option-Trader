from datetime import datetime, timezone

import pytest
import time_machine

# All tests run "on" this date so recorded fixtures (with real expiry dates) never go stale.
TEST_NOW = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)  # Thu 10:00 ET, 19:30 IST


@pytest.fixture(autouse=True)
def frozen_clock():
    with time_machine.travel(TEST_NOW, tick=True):
        yield


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keep tests from touching the real ~/.option_trader directory."""
    from option_trader import config

    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv("OPTION_TRADER_DATA_DIR", str(tmp_path / "data"))
    yield
