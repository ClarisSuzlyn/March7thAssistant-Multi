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
    states = {
        STATE_IN_GAME,
        STATE_TITLE,
        STATE_LOGIN_ICON,
        STATE_POPUP,
        STATE_PHONE_FORM,
        STATE_PASSWORD_FORM,
        STATE_CONFIRM_DIALOG,
        STATE_UNKNOWN,
    }
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


def test_prepare_account_presses_escape_after_unknown_grace(monkeypatch):
    from core import logout

    monkeypatch.setattr(logout, "_load_credentials", lambda account: ("user", "pass"))
    monkeypatch.setattr(logout, "detect_state", lambda *args, **kwargs: STATE_UNKNOWN)
    monkeypatch.setattr(logout, "UNKNOWN_GRACE", 0)
    monkeypatch.setattr(logout.time, "sleep", lambda seconds: None)
    pressed = []
    monkeypatch.setattr(logout, "_press", lambda key: pressed.append(key))

    with pytest.raises(logout.SwitchError):
        logout.prepare_account({"id": "acc", "name": "acc"}, timeout=0.3)

    assert pressed and set(pressed) == {"esc"}


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
