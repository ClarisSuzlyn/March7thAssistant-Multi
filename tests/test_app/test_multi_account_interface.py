"""多账号设置页 UI 测试：卡片构建、密码显隐、显式保存与切号回填。"""
import sys

import pytest

from core import account_manager as am

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


def test_buttons_disabled_without_accounts(group):
    assert group.addButton.isEnabled() is True
    assert group.removeButton.isEnabled() is False
    assert group.saveButton.isEnabled() is False
    assert group.startButton.isEnabled() is False


def test_cards_are_built(group, store):
    store.new_account("A", copy_current=False)
    group._reload_from_store()

    assert group.removeButton.isEnabled() is True
    assert group.saveButton.isEnabled() is True
    assert group.startButton.isEnabled() is True
    assert group.nameEdit.text() == "A"
    assert group.accountGroup.isVisible() or group.accountGroup is not None
    assert group.afterFinishGroup is not None
    assert group.tipLabel is not None


def test_after_finish_options_match_store(group):
    values = [group.afterFinishCombo.itemData(i) for i in range(group.afterFinishCombo.count())]

    assert values == list(am.AFTER_FINISH_OPTIONS)


def test_password_toggle_switches_echo_mode(group):
    from PySide6.QtWidgets import QLineEdit

    assert group.pwdInput.edit.echoMode() == QLineEdit.EchoMode.Password

    group.pwdInput.toggleButton.click()
    assert group.pwdInput.edit.echoMode() == QLineEdit.EchoMode.Normal

    group.pwdInput.toggleButton.click()
    assert group.pwdInput.edit.echoMode() == QLineEdit.EchoMode.Password


def test_editing_does_not_write_until_save(group, store):
    account = store.new_account("A", copy_current=False)
    group._reload_from_store()

    group.nameEdit.setText("改名后")
    assert group._is_dirty() is True
    assert store.get_account(account["id"])["name"] == "A"

    assert group._on_save(silent=True) is True
    assert store.get_account(account["id"])["name"] == "改名后"
    assert group._is_dirty() is False


def test_switching_account_loads_its_fields(group, store, monkeypatch):
    store.new_account("A", copy_current=False)
    second = store.new_account("B", copy_current=False)
    store.update_account(second["id"], after_finish="none")
    monkeypatch.setattr(
        store, "switch_account", lambda account_id, cfg=None: (store.set_current(account_id), True)[1]
    )
    group._reload_from_store()

    group.accountCombo.setCurrentIndex(0)
    assert group.nameEdit.text() == "A"

    group.accountCombo.setCurrentIndex(1)
    assert group.nameEdit.text() == "B"
    assert group.afterFinishCombo.currentData() == "none"


def test_path_switch_toggles_independent_path(group):
    group.pathSwitch.setChecked(False)
    assert group.pathEdit.isEnabled() is True

    group.pathSwitch.setChecked(True)
    assert group.pathEdit.isEnabled() is False
    assert group.browseButton.isEnabled() is False


def test_switching_account_refreshes_original_settings_ui(group, store, monkeypatch, qtbot):
    """切号后必须软刷新原项目设置卡片（只改值，不重建界面）。"""
    from PySide6.QtWidgets import QWidget

    store.new_account("A", copy_current=False)
    store.new_account("B", copy_current=False)
    monkeypatch.setattr(
        store, "switch_account", lambda account_id, cfg=None: (store.set_current(account_id), True)[1]
    )
    group._reload_from_store()

    calls = []

    class _FakeSetting:
        def refresh_values(self):
            calls.append(True)

    fake_window = QWidget()
    fake_window.settingInterface = _FakeSetting()
    fake_window._on_config_file_changed = lambda: calls.append("rebuild")
    monkeypatch.setattr(group, "window", lambda: fake_window)

    group.accountCombo.setCurrentIndex(1)
    qtbot.wait(100)

    assert calls == [True], "切号后应只调用 settingInterface.refresh_values()，不得重建设置页"

# [MULTI_ACCOUNT] 顶部操作栏被设置界面领养后应脱离分组布局


def test_top_bar_is_detached_and_adoptable(group):
    from PySide6.QtWidgets import QWidget

    assert group.topBar.parent() is group
    assert group.topBar.isVisible() is False
    assert group.copyButton.isEnabled() is False

    host = QWidget()
    bar = group.adopt_top_bar(host)

    assert bar is group.topBar
    assert bar.parent() is host
    assert bar.isHidden() is False
