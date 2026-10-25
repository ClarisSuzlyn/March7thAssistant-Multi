# [MULTI_ACCOUNT] 多账号：账号清单 + 每账号独立配置。
"""多账号配置隔离。

方案（尽量少改原项目）：
1. 账号清单、机器级选项存 ``settings/multi_account/accounts.json``。
2. 每个账号的完整配置存 ``settings/multi_account/profiles/<id>.yaml``。
3. 切换账号 = 生成 ``默认配置 + 账号覆盖 + 机器级键`` 合并结果，替换内存里的
   ``cfg.config`` 并原子写回 ``./config.yaml``。
   - GUI 进程读的是内存，配置页刷新后即为该账号的配置；
   - 任务子进程（``python main.py ...``）读的是 ``./config.yaml``，所以必须先落盘。
4. 机器级键（游戏路径、热键、通知、窗口、更新等）永远取「当前正在运行的值」，
   切换账号不会把本机设置带偏。
5. 游戏账号（注册表）绑定复用原项目 ``settings/accounts/<uid>.reg/.acc``，
   登录/过期重登逻辑完全走原项目 ``tasks.game``，不重复实现。
"""

from __future__ import annotations

import base64
import copy
import json
import os
import tempfile
import time
import uuid

from module.logger import log

DATA_DIR = os.path.join("settings", "multi_account")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
STATE_PATH = os.path.join(DATA_DIR, "accounts.json")

STATE_VERSION = 1

DEFAULT_OPTIONS = {
    # 切号前是否尝试「图像/OCR 登出」；失败时仍会关闭游戏客户端
    "auto_logout": True,
    # 某个账号出错后是否继续下一个账号
    "continue_on_error": True,
    # 账号之间的额外等待（秒）
    "switch_delay": 3,
}

# [MULTI_ACCOUNT] 账号级（私有）配置键白名单。
# 只有这些 Tab 的设置随账号变化：体力 / 日常 / 货币战争 / 差分宇宙 / 锄大地 / 逐光捡金。
# 其余（程序 / 云游戏 / 推送 / 杂项 / 账号 / 关于，以及热键、自动启动等）全局共享。
PRIVATE_KEYS = frozenset({
    # 体力
    "power_enable", "power_plan", "instance_type", "calyx_golden_preference", "instance_names",
    "instance_names_challenge_count", "tp_before_instance", "build_target_enable", "build_target_scheme",
    "build_target_ornament_weekly_count", "build_target_use_user_instance_when_only_erosion_and_ornament",
    "break_down_level_four_relicset", "weekly_relic_smart_discard_enable", "weekly_relic_cleanup_day_of_week",
    "instance_team_number", "merge_immersifier", "use_reserved_trailblaze_power", "use_fuel",
    "echo_of_war_enable", "echo_of_war_timestamp", "echo_of_war_start_day_of_week",
    "borrow_enable", "borrow_character_enable", "borrow_friends", "borrow_scroll_times",
    # 日常
    "reward_enable", "reward_dispatch_enable", "reward_mail_enable", "reward_assist_enable",
    "reward_quest_enable", "reward_srpass_enable", "reward_redemption_code_enable",
    "reward_achievement_enable", "reward_message_enable", "daily_enable", "daily_material_enable",
    "daily_himeko_try_enable", "daily_memory_one_enable", "daily_memory_one_team", "last_run_timestamp",
    "activity_enable", "activity_dailycheckin_enable", "activity_gardenofplenty_enable",
    "activity_realmofthestrange_enable", "activity_planarfissure_enable",
    "activity_journey_highlights_notification_enable", "asset_manager_enable",
    "asset_self_molding_resin_enable", "asset_self_molding_resin_timestamp",
    "asset_ember_special_pass_enable", "asset_ember_special_pass_timestamp",
    "asset_ember_regular_pass_enable", "asset_ember_regular_pass_timestamp",
    "asset_ember_tracks_of_destiny_enable", "asset_ember_tracks_of_destiny_timestamp",
    "asset_lc3_star_superimpose_enable",
    # 货币战争
    "currencywars_enable", "currencywars_type", "currencywars_rank_difficulty", "currencywars_timestamp",
    "currencywars_bonus_enable", "currencywars_fast_mode", "currencywars_strategy",
    "currencywars_remembrance_trailblazer_name", "currencywars_strategy_restart_on_special_tags",
    # 差分宇宙
    "weekly_divergent_enable", "weekly_divergent_type", "weekly_divergent_level", "weekly_divergent_timestamp",
    "weekly_divergent_bonus_enable", "weekly_divergent_stable_mode", "divergent_auto_save_enable",
    "universe_enable", "universe_operation_mode", "universe_category", "universe_bonus_enable",
    "universe_frequency", "universe_count", "universe_timeout", "universe_timestamp",
    "divergent_type", "divergent_team_type", "universe_fate", "universe_difficulty",
    # 锄大地
    "universe_enable_gpu", "fight_enable", "fight_operation_mode", "fight_timeout", "fight_team_enable",
    "fight_team_number", "fight_timestamp", "fight_allow_map_buy", "fight_allow_snack_buy",
    "fight_allow_memory_token", "fight_reward_enable", "fight_main_map", "fight_map_version",
    # 逐光捡金
    "forgottenhall_enable", "forgottenhall_level", "forgottenhall_team1", "forgottenhall_team2",
    "forgottenhall_timestamp", "purefiction_enable", "purefiction_level", "purefiction_team1",
    "purefiction_team2", "purefiction_timestamp", "apocalyptic_enable", "apocalyptic_level",
    "apocalyptic_team1", "apocalyptic_team2", "apocalyptic_timestamp",
    # 补充：与原项目各 Tab 的实际配置键对齐（“程序”Tab 的 use_background_screenshot 保持全局）
    "power_plan_keep", "power_limit", "instance_team_enable", "instance_teams",
    "merge_immersifier_limit", "weekly_relic_cleanup_timestamp",
    "activity_gardenofplenty_instance_type", "already_used_codes", "daily_tasks",
    "fight_path", "fight_requirements", "universe_path", "universe_requirements",
    "divergent_station_disabled", "divergent_station_priority", "divergent_station_priority_enable",
    "divergent_universe_daily_completed_count", "divergent_universe_daily_completed_timestamp",
    "divergent_universe_weekly_completed_count", "divergent_universe_weekly_completed_timestamp",
})

# 每账号「任务结束后」行为：inherit = 沿用「程序设置 → 任务完成后操作」（可触发关机等）
AFTER_FINISH_OPTIONS = ("inherit", "logout", "exit", "close", "none")

# 渠道服：0 = 官服，1 = B服（旧数据里可能是 official / bilibili 字符串）
CHANNEL_ALIASES = {"official": 0, "官服": 0, "cn": 0, "bilibili": 1, "b服": 1, "bl": 1}


def normalize_channel(value) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    return CHANNEL_ALIASES.get(str(value or "").strip().lower(), 0)


def is_private_key(key: str) -> bool:
    return key in PRIVATE_KEYS


def is_global_key(key: str) -> bool:
    """非白名单键一律按全局处理：不写进账号 YAML，切号时取当前运行值。"""
    return not is_private_key(key)


def account_after_finish(account: dict | None) -> str:
    value = (account or {}).get("after_finish") or "inherit"
    return value if value in AFTER_FINISH_OPTIONS else "inherit"


def _cfg():
    from module.config import cfg
    return cfg


# --------------------------------------------------------------------------- #
# 状态文件
# --------------------------------------------------------------------------- #
def _ensure_dirs():
    os.makedirs(PROFILES_DIR, exist_ok=True)


def _atomic_write(path: str, text: str):
    _ensure_dirs()
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _default_state() -> dict:
    return {
        "version": STATE_VERSION,
        "current": None,
        "options": dict(DEFAULT_OPTIONS),
        "accounts": [],
    }


def load_state() -> dict:
    """读取账号清单（文件缺失/损坏时返回空状态，不抛异常）。"""
    state = _default_state()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            state["current"] = raw.get("current") or None
            options = raw.get("options")
            if isinstance(options, dict):
                state["options"].update(options)
            accounts = raw.get("accounts")
            if isinstance(accounts, list):
                state["accounts"] = [a for a in accounts if isinstance(a, dict) and a.get("id")]
    except FileNotFoundError:
        pass
    except Exception as e:  # 损坏不致命：按空清单处理，用户可重新建号
        log.warning(f"[多账号] 读取账号清单失败: {e}")

    # 【修改这里】直接把现有的账号列表拿去清理，不要再去调用 list_accounts 了
    valid_ids = {a["id"] for a in state["accounts"]}
    clean_orphan_profiles(valid_ids)
    return state


def save_state(state: dict):
    state = dict(state)
    state["version"] = STATE_VERSION
    _atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_accounts() -> list:
    return load_state()["accounts"]


def get_account(account_id) -> dict | None:
    for account in list_accounts():
        if account["id"] == account_id:
            return account
    return None


def current_id():
    return load_state().get("current")


def current_account() -> dict | None:
    aid = current_id()
    return get_account(aid) if aid else None


def set_current(account_id):
    state = load_state()
    state["current"] = account_id
    save_state(state)


def option(name: str, default=None):
    options = load_state().get("options") or {}
    return options.get(name, DEFAULT_OPTIONS.get(name, default))


def set_options(**kwargs):
    state = load_state()
    options = dict(state.get("options") or {})
    options.update(kwargs)
    state["options"] = options
    save_state(state)


def default_account_name() -> str:
    used = {a.get("name") for a in list_accounts()}
    index = 1
    while f"账号{index}" in used:
        index += 1
    return f"账号{index}"


# --------------------------------------------------------------------------- #
# 增删改
# --------------------------------------------------------------------------- #
def new_account(name: str | None = None, game_uid=None, copy_current: bool = True) -> dict:
    """新建账号：默认以当前运行中的配置为初始配置，方便微调。"""
    _ensure_dirs()
    account = {
        "id": uuid.uuid4().hex[:12],
        "name": (name or default_account_name()).strip() or default_account_name(),
        "game_uid": str(game_uid) if game_uid else "",
        "note": "",
        # [MULTI_ACCOUNT] 账号级登录信息（密码由 DPAPI 加密后存 credential）
        "channel": 0,
        "use_global_path": True,
        "game_path": "",
        "auto_login": True,
        "relogin": False,
        # [MULTI_ACCOUNT] 该账号任务结束后的行为（默认沿用程序设置里的「任务完成后操作」）
        "after_finish": "inherit",
        "credential": "",
        "created_at": int(time.time()),
    }
    if copy_current:
        write_profile(account["id"], copy.deepcopy(dict(_cfg().config)))
    state = load_state()
    state["accounts"].append(account)
    if not state.get("current"):
        state["current"] = account["id"]
    save_state(state)
    log.info(f"[多账号] 已新增账号: {account['name']} ({account['id']})")
    return account


def update_account(account_id, **fields) -> dict | None:
    state = load_state()
    target = None
    for account in state["accounts"]:
        if account["id"] == account_id:
            account.update(fields)
            target = account
            break
    if target is not None:
        save_state(state)
    return target


def copy_account(source_id, new_name: str | None = None) -> dict | None:
    """[MULTI_ACCOUNT] 复制账号：账号级设置与该账号的 YAML 配置整体复制给新账号。"""
    source = get_account(source_id)
    if source is None:
        return None
    name = (new_name or "").strip() or f"{source.get('name') or source_id} 副本"
    account = new_account(name, copy_current=False)
    copied = {
        key: source.get(key)
        for key in (
            "channel",
            "use_global_path",
            "game_path",
            "auto_login",
            "relogin",
            "after_finish",
            "credential",
            "note",
        )
        if key in source
    }
    # 游戏 UID 不复制：新账号首次登录后由调度器重新绑定
    target = update_account(account["id"], **copied) or account
    try:
        source_config = read_profile(source_id)
    except Exception as e:
        log.warning(f"[多账号] 读取源账号配置失败: {e}")
        source_config = {}
    if source_config:
        write_profile(target["id"], copy.deepcopy(source_config))
    log.info(f"[多账号] 已复制账号: {source.get('name')} → {target['name']} ({target['id']})")
    return target


def rename_account(account_id, name: str):
    name = (name or "").strip()
    if name:
        update_account(account_id, name=name)


def bind_game_account(account_id, game_uid):
    """绑定原项目「账户管理」里的游戏账户（settings/accounts/<uid>.reg）。"""
    return update_account(account_id, game_uid=str(game_uid) if game_uid else "")


def remove_account(account_id):
    state = load_state()
    state["accounts"] = [a for a in state["accounts"] if a["id"] != account_id]
    if state.get("current") == account_id:
        state["current"] = state["accounts"][0]["id"] if state["accounts"] else None
    save_state(state)
    try:
        path = profile_path(account_id)
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        log.warning(f"[多账号] 删除账号配置失败: {e}")
    log.info(f"[多账号] 已删除账号: {account_id}")


def move_account(account_id, offset: int):
    """调整执行顺序（offset=-1 上移，+1 下移）。"""
    state = load_state()
    accounts = state["accounts"]
    for index, account in enumerate(accounts):
        if account["id"] != account_id:
            continue
        target = max(0, min(len(accounts) - 1, index + offset))
        if target == index:
            return
        accounts.insert(target, accounts.pop(index))
        save_state(state)
        return


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #
def profile_path(account_id) -> str:
    return os.path.join(PROFILES_DIR, f"{account_id}.yaml")


def _new_yaml():
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml


def read_profile(account_id) -> dict:
    path = profile_path(account_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _new_yaml().load(f)
        return dict(data) if data else {}
    except Exception as e:
        log.error(f"[多账号] 读取账号配置失败 {path}: {e}")
        return {}


def write_profile(account_id, data: dict):
    if not account_id:
        return
    # 账号配置只保留账号级键；机器级键切换时始终取当前运行进程的值
    data = {key: value for key, value in (data or {}).items() if is_private_key(key)}
    _ensure_dirs()
    path = profile_path(account_id)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _new_yaml().dump(dict(data), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_live_config(account_id=None, cfg=None):
    """把内存里的 cfg.config 存回账号配置（切号前 / 点「保存配置」时调用）。"""
    account_id = account_id or current_id()
    if not account_id:
        return
    cfg = cfg or _cfg()
    try:
        write_profile(account_id, copy.deepcopy(dict(cfg.config)))
    except Exception as e:
        log.error(f"[多账号] 保存账号配置失败: {e}")
        return
    # [MULTI_ACCOUNT] 使用全局路径的账号：把当前游戏路径记为机器级默认值
    account = get_account(account_id) or {}
    if account.get("use_global_path", True):
        global_path = cfg.config.get("game_path")
        if global_path:
            state = load_state()
            if state.get("global_game_path") != global_path:
                state["global_game_path"] = global_path
                save_state(state)


_DEFAULT_CACHE: dict | None = None


def _default_config() -> dict:
    """项目自带默认配置（config.example.yaml），作为账号配置的干净底座。

    直接用 cfg.config 做底座会把上一个账号的键值带过来，因此每次从文件重新读取。
    """
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        from module.config import EXAMPLE_PATH
        with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
            _DEFAULT_CACHE = dict(_new_yaml().load(f) or {})
    return copy.deepcopy(_DEFAULT_CACHE)


def _deep_merge(base: dict, override: dict):
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def build_account_config(account_id, live: dict | None = None) -> dict:
    """默认配置 + 账号配置 + 当前机器级键 = 该账号生效配置。"""
    cfg = _cfg()
    live = cfg.config if live is None else live
    merged = _default_config()
    # 兼容旧档案：读到全局键就丢掉，保证严格隔离
    profile = {key: value for key, value in read_profile(account_id).items() if is_private_key(key)}
    _deep_merge(merged, profile)
    for key, value in live.items():
        if is_global_key(key):
            merged[key] = copy.deepcopy(value)
    # [MULTI_ACCOUNT] 游戏路径：独立路径优先，否则用机器级默认值
    account = get_account(account_id) or {}
    state = load_state()
    global_path = state.get("global_game_path") or merged.get("game_path") or ""
    if account.get("use_global_path", True):
        if global_path:
            merged["game_path"] = global_path
    else:
        merged["game_path"] = account.get("game_path") or global_path
    return merged


def apply_account(account_id, cfg=None, save: bool = True) -> dict:
    """把账号配置覆盖到 cfg.config，并写回 ./config.yaml（子进程立即生效）。"""
    cfg = cfg or _cfg()
    merged = build_account_config(account_id, cfg.config)
    cfg.config = merged
    if save:
        cfg.save_config()
    return merged


def switch_account(account_id, cfg=None) -> bool:
    """切换当前账号：先保存当前账号配置，再覆盖为目账号配置。"""
    cfg = cfg or _cfg()
    if not get_account(account_id):
        log.error(f"[多账号] 切换失败，账号不存在: {account_id}")
        return False
    previous = current_id()
    if previous:
        # [MULTI_ACCOUNT] 同一账号也要存：用户在「体力/日常」等页面的改动只在内存里，
        # 不落盘的话 apply_account 会用旧档案覆盖回来（表现为改了配置不生效）
        save_live_config(previous, cfg)
    apply_account(account_id, cfg)
    set_current(account_id)
    log.info(f"[多账号] 已切换到账号: {get_account(account_id).get('name')}")
    return True


def refresh_current_from_config():
    """外部（如配置文件被更新器重写）改动后，把当前账号配置与运行配置对齐。"""
    account = current_account()
    if account:
        save_live_config(account["id"])


# --------------------------------------------------------------------------- #
# 游戏账户（复用原项目 registry 机制）
# --------------------------------------------------------------------------- #
def game_account_label(game_uid) -> str:
    if not game_uid:
        return ""
    uid = str(game_uid)
    name_file = os.path.join("settings", "accounts", f"{uid}.name")
    if os.path.exists(name_file):
        try:
            with open(name_file, "r", encoding="utf-8") as f:
                name = f.read().strip()
            if name:
                return f"{name}({uid})"
        except OSError:
            pass
    return uid


def known_game_accounts() -> list:
    """列出原项目已导出的游戏账户（uid, 显示名）。"""
    result = []
    directory = os.path.join("settings", "accounts")
    if not os.path.isdir(directory):
        return result
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".reg"):
            continue
        uid = name[:-4]
        result.append({"uid": uid, "label": game_account_label(uid)})
    return result


def _dpapi_encrypt(text: str) -> str:
    from utils.encryption import wdp_encrypt
    return base64.b64encode(wdp_encrypt(text.encode("utf-8"))).decode("ascii")


def _dpapi_decrypt(token: str) -> str:
    from utils.encryption import wdp_decrypt
    return wdp_decrypt(base64.b64decode(token.encode("ascii"))).decode("utf-8")


def save_credential(account_id, username: str, password: str):
    """账号密码加密存储（DPAPI，仅本机本用户可解），并同步给原项目自动登录。"""
    token = _dpapi_encrypt(f"{username}\x00{password}")
    account = update_account(account_id, credential=token)
    game_uid = (account or {}).get("game_uid")
    if game_uid and username:
        try:
            from app.tools.account_manager import save_acc_and_pwd
            save_acc_and_pwd(int(game_uid), username, password)
        except Exception as e:
            log.warning(f"[多账号] 同步自动登录凭据失败: {e}")


def load_credential(account_id) -> tuple:
    account = get_account(account_id) or {}
    token = account.get("credential")
    if not token:
        return None, None
    try:
        username, password = _dpapi_decrypt(token).split("\x00", 1)
        return username, password
    except Exception as e:
        log.warning(f"[多账号] 解密账号密码失败: {e}")
        return None, None


def clean_orphan_profiles(valid_ids: set):
    """[MULTI_ACCOUNT] 清理没有在 accounts.json 中登记的废弃 YAML 文件"""
    if not os.path.exists(PROFILES_DIR):
        return

    for filename in os.listdir(PROFILES_DIR):
        if filename.endswith(".yaml"):
            file_id = filename[:-5]  # 去掉 .yaml 后缀
            if file_id not in valid_ids:
                try:
                    os.remove(os.path.join(PROFILES_DIR, filename))
                    log.info(f"[多账号] 自动清理无主配置文件: {filename}")
                except Exception as e:
                    log.warning(f"[多账号] 清理孤儿配置文件失败 {filename}: {e}")