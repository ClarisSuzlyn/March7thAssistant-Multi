"""[MULTI_ACCOUNT] 多账号视觉状态机的纯函数测试。"""

import pytest

from core.logout import (
    LOGIN_STATES,
    STATE_CONFIRM_DIALOG,
    STATE_IN_GAME,
    STATE_LABELS,
    STATE_LOGIN_ICON,
    STATE_PASSWORD_FORM,
    STATE_PHONE_FORM,
    STATE_POPUP,
    STATE_TITLE,
    STATE_UNKNOWN,
)


def test_state_labels_cover_all_states():
    """每个导出的大写 STATE_* 常量都必须在 STATE_LABELS 里有可读名。"""
    from core import logout

    states = {
        value
        for name, value in vars(logout).items()
        if name.startswith("STATE_") and isinstance(value, str)
    }
    assert states, "没有找到任何 STATE_* 常量"
    assert states == set(STATE_LABELS)


def test_login_states_are_subset_of_known_states():
    assert LOGIN_STATES <= set(STATE_LABELS)
    assert STATE_IN_GAME not in LOGIN_STATES
    assert STATE_TITLE not in LOGIN_STATES


def test_detect_state_skips_vision_when_client_not_running(monkeypatch):
    from core import logout

    def _no_vision(*args, **kwargs):
        raise AssertionError("游戏客户端未启动时不应该截图/OCR")

    monkeypatch.setattr(logout, "is_game_running", lambda: False)
    monkeypatch.setattr(logout, "_find_img", _no_vision)
    monkeypatch.setattr(logout, "_find_text", _no_vision)

    assert logout.detect_state() == STATE_UNKNOWN
    assert logout.is_in_game() is False


def test_prepare_account_in_game_without_relogin_returns_true(monkeypatch):
    from core import logout

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", lambda *args, **kwargs: STATE_IN_GAME)

    pressed = []
    monkeypatch.setattr(logout, "_press", lambda key: pressed.append(key))

    assert logout.prepare_account({"id": "acc", "name": "acc"}, timeout=5) is True
    assert pressed == []


def test_prepare_account_relogin_runs_only_once(monkeypatch):
    """勾选「总是重新登录」时只登出一次，登录进游戏后立即返回（不会无限重登）。"""
    from core import logout

    states = [STATE_IN_GAME, STATE_PASSWORD_FORM]
    index = {"i": 0}
    calls = {"logout": 0, "login": 0, "enter": 0}

    def fake_detect_state(*args, **kwargs):
        current = states[min(index["i"], len(states) - 1)]
        index["i"] += 1
        return current

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", fake_detect_state)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)

    def fake_logout(*args, **kwargs):
        calls["logout"] += 1
        return True

    def fake_login(*args, **kwargs):
        calls["login"] += 1
        return True

    def fake_enter(*args, **kwargs):
        calls["enter"] += 1
        return True

    monkeypatch.setattr(logout, "logout_to_login_page", fake_logout)
    monkeypatch.setattr(logout, "login_account", fake_login)
    monkeypatch.setattr(logout, "enter_game", fake_enter)

    account = {"id": "acc", "name": "acc", "auto_login": True, "relogin": True}
    assert logout.prepare_account(account, timeout=30) is True
    assert calls == {"logout": 1, "login": 1, "enter": 1}


def test_prepare_account_title_page_logs_out_before_login(monkeypatch):
    """标题页（开始游戏/点击进入）无法确认账号：自动登录模式先登出再登录。"""
    from core import logout

    states = [STATE_TITLE, STATE_PASSWORD_FORM]
    index = {"i": 0}
    calls = {"logout": 0, "login": 0, "enter": 0}

    def fake_detect_state(*args, **kwargs):
        current = states[min(index["i"], len(states) - 1)]
        index["i"] += 1
        return current

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", fake_detect_state)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(logout, "logout_to_login_page", lambda *a, **k: calls.__setitem__("logout", calls["logout"] + 1) or True)
    monkeypatch.setattr(logout, "login_account", lambda *a, **k: calls.__setitem__("login", calls["login"] + 1) or True)
    monkeypatch.setattr(logout, "enter_game", lambda *a, **k: calls.__setitem__("enter", calls["enter"] + 1) or True)

    account = {"id": "acc", "name": "acc", "auto_login": True, "relogin": False}
    assert logout.prepare_account(account, timeout=30) is True
    assert calls == {"logout": 1, "login": 1, "enter": 1}


def test_prepare_account_does_not_spam_escape_while_loading(monkeypatch):
    from core import logout

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", lambda *args, **kwargs: STATE_UNKNOWN)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)
    pressed = []
    monkeypatch.setattr(logout, "_press", lambda key: pressed.append(key))

    with pytest.raises(logout.SwitchError):
        logout.prepare_account({"id": "acc", "name": "acc"}, timeout=0.3)

    assert pressed == []


def test_prepare_account_never_presses_escape_on_unknown(monkeypatch):
    """未知界面（加载/更新）绝对不按 ESC，只等待直到超时。"""
    from core import logout

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", lambda *args, **kwargs: STATE_UNKNOWN)
    monkeypatch.setattr(logout, "UNKNOWN_GRACE", 0)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)
    pressed = []
    monkeypatch.setattr(logout, "_press", lambda key: pressed.append(key))

    with pytest.raises(logout.SwitchError):
        logout.prepare_account({"id": "acc", "name": "acc"}, timeout=0.3)

    assert pressed == []


def test_prepare_account_clicks_confirm_on_risk_popup(monkeypatch):
    """「账号存在安全风险」弹窗：点【确定】后继续等待登录页。"""
    from core import logout

    sequence = [logout.STATE_RISK_POPUP, STATE_PASSWORD_FORM]
    index = {"i": 0}
    clicks = []

    def fake_detect_state(*args, **kwargs):
        current = sequence[min(index["i"], len(sequence) - 1)]
        index["i"] += 1
        return current

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", fake_detect_state)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(logout, "_click_text", lambda *a, **k: clicks.append(a[0]) or True)
    monkeypatch.setattr(logout, "login_account", lambda *a, **k: True)
    monkeypatch.setattr(logout, "enter_game", lambda *a, **k: True)

    assert logout.prepare_account({"id": "acc", "name": "acc"}, timeout=10) is True
    assert "确定" in clicks


def test_prepare_account_login_failure_raises(monkeypatch):
    from core import logout

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", lambda *args, **kwargs: STATE_PASSWORD_FORM)
    monkeypatch.setattr(logout, "login_account", lambda *a, **k: False)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)

    with pytest.raises(logout.SwitchError):
        logout.prepare_account({"id": "acc", "name": "acc"}, timeout=5)


def test_prepare_account_manual_mode_without_credentials_returns_false(monkeypatch):
    from core import logout

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("", ""))

    account = {"id": "acc", "name": "acc", "auto_login": False}
    assert logout.prepare_account(account, timeout=5) is False


def test_title_logout_presses_escape_when_button_not_found(monkeypatch):
    """标题页模板/OCR 全部没命中时，按两次 ESC 兜底并返回 False。"""
    from core import logout

    monkeypatch.setattr(logout, "detect_state", lambda *args, **kwargs: STATE_TITLE)
    monkeypatch.setattr(logout, "_click_img", lambda *args, **kwargs: False)
    monkeypatch.setattr(logout, "_click_text", lambda *args, **kwargs: False)
    monkeypatch.setattr(logout, "STEP_RETRIES", 1)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)
    pressed = []
    monkeypatch.setattr(logout, "_press", lambda key: pressed.append(key))

    assert logout._title_logout() is False
    assert pressed == ["esc", "esc"]


def test_logout_to_login_page_stops_when_title_logout_fails(monkeypatch):
    """标题页登出失败时应立即返回 False，而不是停在标题页死循环。"""
    from core import logout

    monkeypatch.setattr(logout, "detect_state", lambda *args, **kwargs: STATE_TITLE)
    monkeypatch.setattr(logout, "_title_logout", lambda *args, **kwargs: False)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)
    pressed = []
    monkeypatch.setattr(logout, "_press", lambda key: pressed.append(key))

    assert logout.logout_to_login_page(timeout=5) is False
    assert pressed == []


def test_switch_to_account_without_credentials_returns_false(monkeypatch):
    from core import logout

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("", ""))
    assert logout.switch_to_account({"id": "acc", "name": "acc"}) is False

def test_wait_login_result_clicks_confirm_on_risk_popup(monkeypatch):
    """点击【进入游戏】后弹出「账号存在安全风险」时，必须点【确定】并交回主循环等验证码。"""
    from core import logout

    clicked = []

    monkeypatch.setattr(logout, "_find_img", lambda *a, **k: False)
    monkeypatch.setattr(
        logout,
        "_find_text",
        lambda text, *a, **k: text in ("账号存在安全风险", "安全风险", "验证身份"),
    )
    monkeypatch.setattr(logout, "_click_text", lambda *a, **k: clicked.append(a) or True)
    monkeypatch.setattr(logout, "_click_img", lambda *a, **k: False)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)

    assert logout._wait_login_result(timeout=2) is False
    assert clicked, "检测到安全风险弹窗必须点击【确定】"


def test_wait_login_result_returns_false_on_verify_web(monkeypatch):
    """已经跳到短信验证网页时直接返回 False，交给主循环的 STATE_MANUAL_VERIFY。"""
    from core import logout

    monkeypatch.setattr(logout, "_find_img", lambda *a, **k: False)
    monkeypatch.setattr(logout, "_find_text", lambda text, *a, **k: text in ("安全验证", "获取验证码"))
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)

    assert logout._wait_login_result(timeout=2) is False


def test_looks_like_web_page(monkeypatch):
    """浅色验证网页判定：浅灰底 + 几乎无深色内容；深色游戏画面不成立。"""
    from PIL import Image

    from core import logout
    from module.automation import auto as automation_auto

    monkeypatch.setattr(
        automation_auto,
        "screenshot",
        Image.new("RGB", (1920, 1080), (240, 240, 240)),
        raising=False,
    )
    assert logout._looks_like_web_page() is True

    monkeypatch.setattr(
        automation_auto,
        "screenshot",
        Image.new("RGB", (1920, 1080), (20, 20, 30)),
        raising=False,
    )
    assert logout._looks_like_web_page() is False


class _FakeCfg:
    def __init__(self):
        self.values = {}

    def set_value(self, key, value):
        self.values[key] = value


def test_prepare_resolution_writes_registry_when_client_not_running(monkeypatch):
    from core import scheduler
    from tasks.game import starrailcontroller

    calls = []

    class FakeController:
        def __init__(self, cfg, logger):
            pass

        def is_game_running(self):
            return False

        def change_resolution(self, width, height):
            calls.append(("change", width, height))

    monkeypatch.setattr(starrailcontroller, "StarRailController", FakeController)
    cfg = _FakeCfg()
    scheduler._prepare_resolution(cfg)

    assert cfg.values.get("auto_set_resolution_enable") is True
    assert calls == [("change", 1920, 1080)]


def test_prepare_resolution_restarts_client_when_always_wrong(monkeypatch):
    from core import logout
    from core import scheduler
    from tasks.game import starrailcontroller

    calls = []

    class FakeController:
        def __init__(self, cfg, logger):
            pass

        def is_game_running(self):
            return True

        def get_resolution(self):
            return (1600, 900)

        def change_resolution(self, width, height):
            calls.append(("change", width, height))

    monkeypatch.setattr(starrailcontroller, "StarRailController", FakeController)
    monkeypatch.setattr(logout, "close_game_client", lambda: calls.append(("close",)))
    monkeypatch.setattr(scheduler.time, "sleep", lambda seconds: None)

    scheduler._prepare_resolution(_FakeCfg())

    assert ("close",) in calls, "3 次仍不对时应关闭客户端"
    assert calls[-1] == ("change", 1920, 1080), "关闭后应重新写入 1920x1080"
