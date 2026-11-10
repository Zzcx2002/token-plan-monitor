import os

import pytest

from token_plan_monitor.bailian import BailianClient
from token_plan_monitor.models import ConfigurationError, Settings
from token_plan_monitor.storage import Storage
from token_plan_monitor.volcengine import VolcengineClient

pytestmark = pytest.mark.live


def test_real_volcengine_quota_when_explicitly_enabled(capsys):
    if os.environ.get("TOKEN_PLAN_MONITOR_LIVE") != "1":
        pytest.skip("Set TOKEN_PLAN_MONITOR_LIVE=1 to enable the real OpenAPI test")
    storage = Storage()
    settings = storage.load_settings()
    credentials = storage.load_credentials()
    if credentials is None:
        pytest.skip("Save AK/SK in the application settings before the live test")
    snapshot = VolcengineClient(settings, credentials).query()
    assert snapshot.windows
    with capsys.disabled():
        print(f"\n{snapshot.plan_name}")
        for window in snapshot.windows:
            print(
                f"{window.kind}: used={window.used_percent:.2f}%, "
                f"remaining={window.remaining_percent:.2f}%"
            )


def test_real_bailian_quota_when_explicitly_enabled(capsys):
    if os.environ.get("TOKEN_PLAN_MONITOR_LIVE") != "1":
        pytest.skip("Set TOKEN_PLAN_MONITOR_LIVE=1 to enable the real OpenAPI test")
    storage = Storage()
    stored = storage.load_settings()
    try:
        settings = Settings(provider="bailian", region=stored.region)
    except ConfigurationError:
        settings = Settings(provider="bailian")
    credentials = storage.load_credentials("bailian")
    if credentials is None:
        pytest.skip("Save the Bailian AK/SK in the application settings before the live test")
    snapshot = BailianClient(settings, credentials).query()
    assert snapshot.windows
    with capsys.disabled():
        print(f"\n{snapshot.plan_name}")
        for window in snapshot.windows:
            print(
                f"{window.kind}: used={window.used_percent:.2f}%, "
                f"remaining={window.remaining_percent:.2f}%"
            )
