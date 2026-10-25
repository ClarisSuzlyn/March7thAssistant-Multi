"""多账号账号库单元测试：账号增删改、配置档案、凭据加密与全局键隔离。"""

import json
import os

import pytest

from core import account_manager as am


@pytest.fixture()
def store(tmp_path, monkeypatch):
    data_dir = tmp_path / "multi_account"
    monkeypatch.setattr(am, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(am, "PROFILES_DIR", str(data_dir / "profiles"))
    monkeypatch.setattr(am, "STATE_PATH", str(data_dir / "accounts.json"))
    monkeypatch.setattr(am, "_DEFAULT_CACHE", None)
    return am


def test_new_account_becomes_current(store):
    account = store.new_account("主账号", copy_current=False)

    assert account["id"]
    assert account["name"] == "主账号"
    assert account["channel"] == 0
    assert account["after_finish"] == "inherit"
    assert account["auto_login"] is True
    assert store.current_id() == account["id"]
    assert store.current_account()["name"] == "主账号"


def test_default_account_names_are_incremental(store):
    names = [store.new_account(copy_current=False)["name"] for _ in range(3)]

    assert names == ["账号1", "账号2", "账号3"]


def test_update_rename_and_remove(store):
    account = store.new_account("A", copy_current=False)

    store.update_account(account["id"], auto_login=False, relogin=True)
    assert store.get_account(account["id"])["auto_login"] is False
    assert store.get_account(account["id"])["relogin"] is True

    store.rename_account(account["id"], "B")
    assert store.get_account(account["id"])["name"] == "B"

    store.remove_account(account["id"])
    assert store.list_accounts() == []
    assert store.current_id() is None


def test_move_account_reorders(store):
    ids = [store.new_account(f"账号{index}", copy_current=False)["id"] for index in range(3)]

    store.move_account(ids[2], -1)

    assert [account["id"] for account in store.list_accounts()] == [ids[0], ids[2], ids[1]]
    store.move_account(ids[0], 99)
    assert [account["id"] for account in store.list_accounts()] == [ids[2], ids[1], ids[0]]


def test_profile_only_keeps_account_level_keys(store):
    account = store.new_account("A", copy_current=False)

    store.write_profile(
        account["id"],
        {"daily_enable": True, "after_finish": "Shutdown", "notify_enable": True},
    )

    profile = store.read_profile(account["id"])
    assert profile["daily_enable"] is True
    assert "after_finish" not in profile
    assert "notify_enable" not in profile


def test_remove_account_deletes_profile(store):
    account = store.new_account("A", copy_current=False)
    store.write_profile(account["id"], {"daily_enable": True})
    path = store.profile_path(account["id"])
    assert os.path.exists(path)

    store.remove_account(account["id"])

    assert not os.path.exists(path)


def test_credential_roundtrip_is_encrypted(store):
    account = store.new_account("A", copy_current=False)

    store.save_credential(account["id"], "user@example.com", "p@ssw0rd!")

    assert store.load_credential(account["id"]) == ("user@example.com", "p@ssw0rd!")
    with open(store.STATE_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    stored = raw["accounts"][0]["credential"]
    assert stored
    assert "p@ssw0rd!" not in stored


def test_global_key_classification(store):
    assert store.is_global_key("after_finish")
    assert store.is_global_key("notify_webhook_url")
    assert store.is_global_key("hotkey_fight")
    assert not store.is_global_key("daily_enable")
    assert not store.is_global_key("instance_type")
    # 白名单外的新键默认按全局处理，避免把机器级配置写进账号档案
    assert store.is_global_key("some_future_global_key")


def test_channel_normalization(store):
    assert store.normalize_channel(0) == 0
    assert store.normalize_channel(1) == 1
    assert store.normalize_channel("official") == 0
    assert store.normalize_channel("bilibili") == 1
    assert store.normalize_channel("官服") == 0
    assert store.normalize_channel(None) == 0


def test_after_finish_option(store):
    assert store.account_after_finish(None) == "inherit"
    assert store.account_after_finish({}) == "inherit"
    assert store.account_after_finish({"after_finish": "logout"}) == "logout"
    assert store.account_after_finish({"after_finish": "unknown"}) == "inherit"


def test_format_duration():
    from core.scheduler import _format_duration

    assert _format_duration(59) == "59 秒"
    assert _format_duration(125) == "2 分 5 秒"
    assert _format_duration(3725) == "1 小时 2 分"


def test_switch_same_account_keeps_live_private_edits(store):
    """用户在「日常/体力」页改的开关只在内存里：再次切换同一账号（启动当前账号）不能丢。"""

    class FakeCfg:
        def __init__(self):
            self.config = {"daily_enable": False, "game_path": "C:/StarRail.exe"}
            self.saved = 0

        def save_config(self):
            self.saved += 1

    account = store.new_account("A", copy_current=False)
    fake = FakeCfg()
    store.switch_account(account["id"], fake)

    fake.config["daily_enable"] = True  # 用户刚在设置页改完，还没点保存
    store.switch_account(account["id"], fake)  # 点「启动当前账号配置」会再次应用同一账号

    assert fake.config["daily_enable"] is True
    assert fake.saved >= 1


def test_run_multi_account_without_accounts(store, monkeypatch):
    from core.scheduler import run_multi_account

    assert run_multi_account() == 1


# [MULTI_ACCOUNT] 复制账号：账号级字段 + YAML 配置整体复制，游戏 UID 不复制


def test_copy_account_copies_fields_and_profile(store):
    source = store.new_account("源账号", copy_current=False)
    store.update_account(
        source["id"],
        channel="bilibili",
        use_global_path=False,
        game_path="D:/games/StarRail.exe",
        auto_login=False,
        relogin=True,
        after_finish="exit",
    )
    store.write_profile(source["id"], {"power_enable": False, "daily_enable": True})

    copied = store.copy_account(source["id"], "副本")

    assert copied is not None
    assert copied["id"] != source["id"]
    assert copied["name"] == "副本"
    assert copied["channel"] == "bilibili"
    assert copied["game_path"] == "D:/games/StarRail.exe"
    assert copied["auto_login"] is False
    assert copied["relogin"] is True
    assert copied["after_finish"] == "exit"
    assert copied["game_uid"] == ""
    assert store.read_profile(copied["id"]) == {"power_enable": False, "daily_enable": True}


def test_copy_account_default_name_and_missing_source(store):
    source = store.new_account("主号", copy_current=False)

    copied = store.copy_account(source["id"])

    assert copied is not None
    assert copied["name"] == "主号 副本"
    assert store.copy_account("missing") is None
