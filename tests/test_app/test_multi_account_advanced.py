# [MULTI_ACCOUNT] 多账号高级功能测试：内存黑匣子、更新提醒、启动前拦截。
"""覆盖入口：内存黑匣子采样/闪退落盘、更新检查比较、启动前校验（云游戏/渠道服/防抖）。"""
import sys
from pathlib import Path

import pytest

from core import account_manager as am
from core import memory_monitor as mm
from core import update_check as uc

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not hasattr(sys, "getwindowsversion"),
    reason="GUI 测试仅在 Windows 平台运行",
)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data_dir = tmp_path / "multi_account"
    monkeypatch.setattr(am, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(am, "PROFILES_DIR", str(data_dir / "profiles"))
    monkeypatch.setattr(am, "STATE_PATH", str(data_dir / "accounts.json"))
    monkeypatch.setattr(am, "_DEFAULT_CACHE", None)
    return am


@pytest.fixture()
def group(qapp, store):
    from app.sub_interfaces.multi_account_interface import MultiAccountGroup

    widget = MultiAccountGroup()
    yield widget
    widget.deleteLater()


# --------------------------------------------------------------------------
# 更新检查
# --------------------------------------------------------------------------
def test_parse_version_handles_prefix_and_short_versions():
    assert uc._parse_version("v2026.10.16") == (2026, 10, 16)
    assert uc._parse_version("2026.10") == (2026, 10)
    assert uc._parse_version("") == (0,)


def test_check_latest_only_reports_newer(monkeypatch):
    monkeypatch.setattr(uc, "fetch_latest", lambda timeout=8.0: ("v2099.1.1", "https://example.com/r"))
    assert uc.check_latest("2026.10.16") == ("v2099.1.1", "https://example.com/r")

    monkeypatch.setattr(uc, "fetch_latest", lambda timeout=8.0: ("v2026.1.1", ""))
    assert uc.check_latest("2026.10.16") is None


def test_check_latest_swallows_network_errors(monkeypatch):
    def boom(timeout=8.0):
        raise RuntimeError("no network")

    monkeypatch.setattr(uc, "fetch_latest", boom)
    assert uc.check_latest("2026.10.16") is None


# --------------------------------------------------------------------------
# 内存黑匣子
# --------------------------------------------------------------------------
def test_memory_monitor_dumps_crash_when_process_disappears(tmp_path, monkeypatch):
    crash_dir = tmp_path / "crash_logs"
    monkeypatch.setattr(mm, "CRASH_DIR", str(crash_dir))
    monkeypatch.setattr(mm, "_tail_log", lambda max_lines=200: "# 假日志\n")

    monitor = mm.MemoryMonitor()
    monkeypatch.setattr(mm, "_read_memory", lambda: (True, 512.0))
    monitor.arm()

    monkeypatch.setattr(mm, "_read_memory", lambda: (False, 0.0))
    monitor.poll()

    files = list(crash_dir.glob("crash_*.log"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "疑似闪退" in text
    assert "峰值内存" in text
    assert "# 假日志" in text
    monitor.disarm()


def test_memory_monitor_ignores_disarmed_disappearance(tmp_path, monkeypatch):
    crash_dir = tmp_path / "crash_logs"
    monkeypatch.setattr(mm, "CRASH_DIR", str(crash_dir))
    monkeypatch.setattr(mm, "_tail_log", lambda max_lines=200: "")

    monitor = mm.MemoryMonitor()
    monkeypatch.setattr(mm, "_read_memory", lambda: (True, 512.0))
    monitor.arm()
    monitor.disarm()

    monkeypatch.setattr(mm, "_read_memory", lambda: (False, 0.0))
    monitor.poll()

    assert not crash_dir.exists() or not list(crash_dir.glob("crash_*.log"))


def test_memory_monitor_samples_are_bounded(monkeypatch):
    monkeypatch.setattr(mm, "CRASH_DIR", "/tmp/unused")
    monitor = mm.MemoryMonitor()
    monkeypatch.setattr(mm, "_read_memory", lambda: (True, 1.0))
    for _ in range(mm.MAX_SAMPLES + 50):
        monitor.poll()
    assert len(monitor.snapshot()) == mm.MAX_SAMPLES


# --------------------------------------------------------------------------
# 启动前拦截
# --------------------------------------------------------------------------
def test_start_blocked_when_cloud_game_enabled(group, store, monkeypatch):
    store.new_account("A", copy_current=False)
    group._reload_from_store()

    monkeypatch.setattr(
        "module.config.cfg.get_value",
        lambda key, default=None, *args, **kwargs: True if key == "cloud_game_enable" else default,
    )
    assert group._prepare_start() is False


def test_start_blocked_for_unsupported_channel(group, store, monkeypatch):
    account = store.new_account("A", copy_current=False)
    store.update_account(account["id"], channel=1)
    group._reload_from_store()

    monkeypatch.setattr("module.config.cfg.get_value", lambda key, default=None, *a, **k: default)
    assert group._prepare_start() is False


def test_start_debounce_blocks_second_click(group, store, monkeypatch):
    store.new_account("A", copy_current=False)
    group._reload_from_store()

    monkeypatch.setattr("module.config.cfg.get_value", lambda key, default=None, *a, **k: default)
    group._starting = True
    assert group._prepare_start() is False

    group._starting = False
    monkeypatch.setattr(store, "switch_account", lambda account_id, cfg=None: True)
    assert group._prepare_start() is True


def test_start_clears_after_timer(group, store, monkeypatch):
    monkeypatch.setattr("module.config.cfg.get_value", lambda key, default=None, *a, **k: default)
    group._begin_starting()
    assert group._starting is True
    assert group.startButton.isEnabled() is False

    group._clear_starting()
    assert group._starting is False
    assert group.startButton.isEnabled() is True
