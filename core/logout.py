# [MULTI_ACCOUNT] 账号登出 / 登录：状态机 + 图像识别 + OCR 双校验 + 分步重试回退。
"""视觉切号模块（多账号轮询靠它切号，不依赖改注册表）。

界面状态（每一步都会打日志，方便在左下角日志浮窗里看进度）：

===============  ==================================================
状态             识别方式
===============  ==================================================
游戏内           左下角 OCR 到 ``UID:数字``，或手机 / 组队 / 头像图标
标题界面         底部「开始游戏 / 点击进入」
标题·未登录      右侧「mi 登入」（mi_login.png，或 OCR「登入」）
选账号弹窗       「进入游戏」+「登录其他账号」同时存在
验证码登录页     「账号密码」入口可见
账号密码页       「输入手机号/邮箱」+「输入密码」
确认弹窗         「是否返回登录页面？」+「确认」
===============  ==================================================

登出 SOP（游戏内或标题界面 → 登录界面）：
    游戏内：ESC 退回主界面（识别到主界面图标再按 ESC 呼出菜单）
            → 点右下角电源图标（含红点变体）
            → 「是否返回登录页面？」点确认 → 回到标题
    标题：点「mi 登出」→「退出登录」弹窗「退出」→ 标题·未登录 / 选账号弹窗

登录 SOP（任意登录界面 → 游戏内）：
    标题·未登录 → 点「mi 登入」
    选账号弹窗 → 点「登录其他账号」
    验证码登录页 → 点「账号密码」
    账号密码页 → 剪贴板粘贴手机号/邮箱、密码 → 勾选协议 → 「进入游戏」
    → 标题界面「开始游戏」→「点击进入」→ 游戏内（并校验 UID）

失败策略：每个步骤 3 次 × 1.5 s，重试时回退上一步（再按 ESC / 点空白）；
密码错误立即抛 :class:`PasswordError`（由调度器通知并跳过该账号）；
登出 + 登录整体超过 3 分钟视为熔断，关闭客户端重来。
"""

from __future__ import annotations

import gc
import time

from module.logger import log

IMG = "./assets/images/screen/"
MA = IMG + "multi_account/"
SRA = MA + "sra/"
QQ = MA + "qq/"
OLD = MA + "legacy/"

# 登录页 / 标题页
MIHOYO_LOGO = SRA + "login_page.png"  # miHoYo 水印：登录弹窗（登录页）
LOGIN_OTHER = QQ + "switch_other_game.png"  # 「登录其他账号」
LOGIN_ICON = SRA + "mi_login.png"  # 标题页右侧「mi 登入」
ACC_PWD_TAB = SRA + "login_with_account.png"  # 「账号密码」
ACC_PWD_TAB_ALT = QQ + "account_pwd.png"
USERNAME_INPUT = SRA + "username_input.png"  # 「输入手机号/邮箱」
PASSWORD_INPUT = QQ + "input_pwd.png"  # 「输入密码」
ENTER_GAME_BTN = SRA + "enter_game.png"  # 黄色「进入游戏」
AGREE_ROW = QQ + "uncheck_agreement.png"  # 未勾选的「同意协议」整行
KEEP_LOGIN_ROW = QQ + "save_login_exit.png"  # 「退出并保留登录记录」行
UNCHECKED_BOX = QQ + "unchecked_box.png"
EXIT_LOGIN_BTN = QQ + "signout_account.png"  # 「退出登录」弹窗的黄色「退出」
POP_CONFIRM_BTN = QQ + "pop_confirm_btn.png"  # 「是否返回登录页面」的「确认」
PASSWORD_ERROR = QQ + "pwd_wrong.png"  # 「账号或密码错误」

# 游戏内
MAIN_ICONS = (
    QQ + "phone_btn.png",
    QQ + "team_up_btn.png",
    QQ + "char_img.png",
    OLD + "home_avatar.png",
    OLD + "home_menu.png",
)

# 后台截图全黑检测（独占全屏下 PrintWindow 会是黑图，UID/模板都识别不到）
_black_frame_checked_at = 0.0
_black_frame_warned_at = 0.0
POWER_BTN = SRA + "power.png"  # 菜单右下角电源
POWER_BTN_ALT = OLD + "power_btn.png"  # 带红点的电源
LOGOUT_ROW = QQ + "logout_account.png"  # mi 图标 + 登出
LOGOUT_ICON = SRA + "logout.png"  # 仅 mi 图标
LOGOUT_ROW_ALT = OLD + "logout_btn.png"  # 1920x1080 版「mi 登出」
LOGOUT_TEXT_ALT = OLD + "logout_text.png"  # 「登出」文字
LOGOUT_BACK = QQ + "back_login_ui.png"  # 标题页右侧电源（退出游戏）

# 标题界面 / 进入游戏
START_GAME_BTN = QQ + "start_game_btn.png"
CLICK_ENTER_BTN = QQ + "click_enter_btn.png"
WELCOME = SRA + "welcome.png"
TRAIN_SUPPLY = SRA + "train_supply.png"  # 月卡弹窗

# OCR 区域（相对坐标）
REGION_RIGHT = (0.92, 0.08, 0.99, 0.46)  # 标题页右侧「登入 / 登出 / 退出」竖排
REGION_DIALOG = (0.28, 0.18, 0.72, 0.82)  # 登录弹窗
REGION_DIALOG_BOTTOM = (0.30, 0.52, 0.70, 0.86)  # 登录弹窗底部按钮
REGION_TITLE_BOTTOM = (0.30, 0.72, 0.70, 0.98)  # 标题页「开始游戏 / 点击进入」
REGION_CONFIRM = (0.52, 0.50, 0.76, 0.66)  # 弹窗「确认」
REGION_LOGOUT_BTN = (0.34, 0.50, 0.64, 0.68)  # 「退出登录」弹窗里的橙色「退出」按钮

THRESHOLD = 0.9
POLL = 1.0
STEP_RETRIES = 3
STEP_INTERVAL = 1.5
GLOBAL_TIMEOUT = 120
UNKNOWN_GRACE = 10  # 识别不到界面多久后按一次 ESC

# 界面状态
STATE_IN_GAME = "in_game"
STATE_TITLE = "title"
STATE_LOGIN_ICON = "login_icon"
STATE_POPUP = "popup"
STATE_PHONE_FORM = "phone_form"
STATE_PASSWORD_FORM = "password_form"
STATE_CONFIRM_DIALOG = "confirm_dialog"
STATE_UNKNOWN = "unknown"

STATE_LABELS = {
    STATE_IN_GAME: "游戏内",
    STATE_TITLE: "标题界面",
    STATE_LOGIN_ICON: "标题·未登录（登入按钮）",
    STATE_POPUP: "选账号弹窗（登录其他账号）",
    STATE_PHONE_FORM: "验证码登录页",
    STATE_PASSWORD_FORM: "账号密码页",
    STATE_CONFIRM_DIALOG: "返回登录页面弹窗",
    STATE_UNKNOWN: "未知界面",
}
LOGIN_STATES = frozenset({STATE_LOGIN_ICON, STATE_POPUP, STATE_PHONE_FORM, STATE_PASSWORD_FORM})


class SwitchError(RuntimeError):
    """切号流程失败（可重试）。"""


class PasswordError(RuntimeError):
    """游戏提示账号或密码错误（不重试，跳过该账号）。"""


# ------------------------------------------------------------------ helpers
def _auto():
    from module.automation import auto

    return auto


def _controller():
    from module.game import get_game_controller

    return get_game_controller()


def _release_automation_images() -> None:
    """[MULTI_ACCOUNT] 断开自动化模块对整帧截图的引用（长时间轮询防内存累积）。"""
    try:
        auto = _auto()
        release = getattr(auto, "release_screenshot", None)
        if callable(release):
            release()
            return
        auto.screenshot = None
        auto.screenshot_pos = None
        auto.ocr_result = None
    except Exception as e:
        log.debug(f"[多账号] 释放截图内存失败: {e}")


def _collect_garbage() -> None:
    """[MULTI_ACCOUNT] 低频节点：释放整帧截图并回收一次内存（OpenCV 大数组碎片）。"""
    _release_automation_images()
    gc.collect()
    log.debug("[多账号] 已强制回收内存")


def _find_img(path: str, threshold: float = THRESHOLD, crop=(0, 0, 1, 1), retries: int = 1) -> bool:
    try:
        return bool(_auto().find_element(path, "image", threshold, retries, crop=crop))
    except Exception as e:
        log.debug(f"[多账号] 查找模板 {path} 失败: {e}")
        return False


def _click_img(path: str, threshold: float = THRESHOLD, crop=(0, 0, 1, 1), retries: int = 2, offset=(0, 0)) -> bool:
    try:
        return bool(_auto().click_element(path, "image", threshold, retries, crop=crop, offset=offset))
    except Exception as e:
        log.debug(f"[多账号] 点击模板 {path} 失败: {e}")
        return False


def _find_text(text: str, include: bool = False, crop=(0, 0, 1, 1)) -> bool:
    try:
        return bool(_auto().find_element(text, "text", include=include, crop=crop))
    except Exception as e:
        log.debug(f"[多账号] 查找文字 {text} 失败: {e}")
        return False


def _click_text(text: str, include: bool = False, crop=(0, 0, 1, 1), offset=(0, 0), retries: int = 2) -> bool:
    try:
        return bool(_auto().click_element(text, "text", include=include, crop=crop, offset=offset, max_retries=retries))
    except Exception as e:
        log.debug(f"[多账号] 点击文字 {text} 失败: {e}")
        return False


def _press(key: str):
    _auto().press_key(key)


def _wait(condition, timeout: float, period: float = POLL) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if condition():
                return True
        except Exception as e:
            log.debug(f"[多账号] 等待条件异常: {e}")
        time.sleep(period)
    return False


def _step(name: str, action, retries: int = STEP_RETRIES, interval: float = STEP_INTERVAL, fallback=None) -> bool:
    """分步重试；重试前执行回退动作（例如再按一次 ESC），并打印每次尝试。"""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            if action():
                return True
        except (PasswordError, SwitchError):
            raise
        except Exception as e:
            last_error = e
            log.debug(f"[多账号] {name} 第 {attempt} 次异常: {e}")
        if attempt < retries:
            log.info(f"[多账号] {name} 失败，第 {attempt}/{retries} 次重试…")
            time.sleep(interval)
            if fallback is not None:
                try:
                    fallback()
                except Exception as e:
                    log.debug(f"[多账号] {name} 回退动作异常: {e}")
    log.error(f"[多账号] {name} 失败" + (f": {last_error}" if last_error else ""))
    return False


def _paste(text: str):
    """剪贴板粘贴（避免中文输入法逐字输入出错），剪贴板不可用时退回逐字输入。"""
    try:
        import pyautogui

        from utils.clipboard import copy

        copy(text)
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "v")
    except Exception as e:
        log.warning(f"[多账号] 剪贴板粘贴失败，改用逐字输入: {e}")
        from tasks.game import ensure_IME_lang_en

        ensure_IME_lang_en()
        time.sleep(0.3)
        _auto().secretly_write(text, interval=0.05)


def _clear_and_paste(text: str):
    import pyautogui

    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.2)
    _paste(text)


def _click_screen_center(ratio: float = 0.5):
    auto = _auto()
    auto.take_screenshot()
    if auto.screenshot is None:
        return False
    height, width = auto.screenshot.shape[:2]
    return bool(auto.click_element_with_pos((width // 2, int(height * ratio))))


# ------------------------------------------------------------- page states
def is_game_running() -> bool:
    """游戏客户端进程是否存在（不触发截图，避免没有窗口时刷「截图失败」日志）。"""
    try:
        return bool(_controller().is_game_running())
    except Exception as e:
        log.debug(f"[多账号] 检测游戏进程失败: {e}")
        return False


def detect_state(log_state: bool = False) -> str:
    """识别当前界面状态（顺序即优先级）。"""
    if not is_game_running():
        # 客户端没启动时不做任何截图/OCR，避免刷「截图失败：没有找到游戏窗口」
        return STATE_UNKNOWN
    try:
        if _find_img(POP_CONFIRM_BTN) or _find_text("是否返回登录页面", crop=REGION_DIALOG):
            state = STATE_CONFIRM_DIALOG
        elif _find_img(LOGIN_OTHER) or _find_text("登录其他账号", include=True, crop=(0.30, 0.55, 0.70, 0.95)):
            state = STATE_POPUP
        elif _find_img(PASSWORD_INPUT) or (_find_img(USERNAME_INPUT) and _find_img(ENTER_GAME_BTN)):
            state = STATE_PASSWORD_FORM
        elif _find_img(ACC_PWD_TAB) or _find_img(ACC_PWD_TAB_ALT):
            state = STATE_PHONE_FORM
        elif (
            _find_img(START_GAME_BTN)
            or _find_text("开始游戏", include=True, crop=REGION_TITLE_BOTTOM)
            or _find_img(CLICK_ENTER_BTN)
            or _find_text("点击进入", include=True, crop=REGION_TITLE_BOTTOM)
        ):
            state = STATE_TITLE
        elif _find_img(LOGIN_ICON) or _find_text("登入", crop=REGION_RIGHT):
            state = STATE_LOGIN_ICON
        elif is_in_game():
            state = STATE_IN_GAME
        else:
            state = STATE_UNKNOWN
    except Exception as e:
        log.debug(f"[多账号] 识别界面状态异常: {e}")
        state = STATE_UNKNOWN
    log.debug(f"[多账号] 当前状态识别为：{STATE_LABELS.get(state, state)}")
    if log_state:
        log.info(f"[多账号] 当前界面：{STATE_LABELS.get(state, state)}")
    return state


def is_login_page() -> bool:
    """当前是否处于登录界面（登入按钮 / 选账号弹窗 / 验证码页 / 账号密码页）。"""
    return detect_state() in LOGIN_STATES


def is_title_page() -> bool:
    """标题界面（开始游戏 / 点击进入 / 登入按钮）。"""
    return detect_state() in (STATE_TITLE, STATE_LOGIN_ICON)


def is_in_game() -> bool:
    """游戏内：主界面图标命中（手机 / 组队 / 头像等）。

    不依赖左下角 UID：全屏 + 悬浮窗遮挡时 UID 常常读不到，卡死风险大于收益。
    识别不到时由调用方在 10 秒后按 ESC 回退，直到呼出主界面。
    """
    if not is_game_running():
        return False
    if any(_find_img(path) for path in MAIN_ICONS):
        return True
    _warn_black_frame()
    return False


def _warn_black_frame() -> None:
    """后台截图整张全黑时提示切换「无边框窗口化」（独占全屏下 PrintWindow 返回黑图）。"""
    global _black_frame_checked_at, _black_frame_warned_at
    now = time.time()
    if now - _black_frame_checked_at < 30 or now - _black_frame_warned_at < 600:
        return
    _black_frame_checked_at = now
    try:
        result = _auto().take_screenshot(use_background_screenshot=True)
    except Exception as e:
        log.debug(f"[多账号] 黑屏检测失败: {e}")
        return
    if not result:
        return
    try:
        from numpy import asarray

        mean = float(asarray(result[0]).mean())
    except Exception:
        return
    if mean >= 8:
        return
    _black_frame_warned_at = now
    log.warning(
        f"[多账号] 后台截图整张全黑（平均像素 {mean:.1f}）：游戏可能为独占全屏，"
        "请把游戏显示模式改为「无边框窗口化」，否则 UID/界面识别会失效"
    )


def wait_login_state(timeout: float = 120) -> bool:
    return bool(_wait(lambda: detect_state() in LOGIN_STATES, timeout))


# --------------------------------------------------------------- lifecycle
def ensure_client_running(timeout: float = 30, quiet: bool = False) -> bool:
    """确保游戏客户端已启动并切到前台（没启动就拉起来）。"""
    controller = _controller()
    if controller.is_game_running():
        if not quiet:
            log.info("[多账号] 游戏客户端已在运行")
        controller.switch_to_game()
        return True
    log.info("[多账号] 未检测到游戏客户端，正在启动…")
    try:
        if not controller.start_game_process():
            log.error("[多账号] 启动游戏进程失败")
            return False
    except Exception as e:
        log.error(f"[多账号] 启动游戏进程失败: {e}")
        return False
    if not _wait(lambda: controller.is_game_running() and controller.switch_to_game(), timeout, 3):
        log.error("[多账号] 等待游戏窗口超时")
        return False
    log.info("[多账号] 游戏客户端已启动，等待界面就绪")
    time.sleep(5)
    return True


def close_game_client(timeout: float = 60) -> bool:
    controller = _controller()
    try:
        if not controller.is_game_running():
            return True
        controller.stop_game()
    except Exception as e:
        log.error(f"[多账号] 关闭游戏失败: {e}")
        return False
    if _wait(lambda: not controller.is_game_running(), timeout):
        log.info("[多账号] 游戏客户端已退出")
        return True
    log.warning("[多账号] 等待游戏退出超时")
    return False


# ------------------------------------------------------------- 登出 SOP
def _title_logout(timeout: float = 90) -> bool:
    """标题界面：点 mi「登出」→「退出登录」弹窗点「退出」→ 登录界面。"""
    if detect_state() in LOGIN_STATES:
        return True

    log.info("[多账号] 标题界面：正在退出当前账号登录")
    for attempt in range(1, STEP_RETRIES + 1):
        # 优先带 mi 图标的整行模板（图2），其次 mi 图标、「登出」文字模板，最后右侧区域 OCR
        clicked = (
            _click_img(LOGOUT_ROW)
            or _click_img(LOGOUT_ROW_ALT)
            or _click_img(LOGOUT_ICON)
            or _click_img(LOGOUT_TEXT_ALT)
            or _click_text("登出", crop=REGION_RIGHT)
        )
        if not clicked:
            log.info(f"[多账号] 未找到【登出】按钮，第 {attempt}/{STEP_RETRIES} 次重试…")
            time.sleep(STEP_INTERVAL)
            continue
        log.info("[多账号] 已点击【登出】，等待「退出登录」弹窗")
        if not _wait(
            lambda: _find_img(EXIT_LOGIN_BTN) or _find_img(KEEP_LOGIN_ROW) or _find_text("退出登录", crop=REGION_DIALOG),
            8,
        ):
            log.info("[多账号] 未出现「退出登录」弹窗，重试…")
            continue
        # 确认「退出并保留登录记录」已勾选（保持该选项，切号才能看到已登录账号）
        if _find_img(UNCHECKED_BOX):
            log.warning("[多账号] 「退出并保留登录记录」未勾选，点击左侧圆圈")
            _click_img(KEEP_LOGIN_ROW, offset=(-150, 0))
            time.sleep(0.5)
        if not (_click_img(EXIT_LOGIN_BTN) or _click_text("退出", crop=REGION_LOGOUT_BTN)):
            log.info("[多账号] 未找到弹窗里的【退出】按钮，重试…")
            continue
        log.info("[多账号] 已点击【退出】，等待回到登录界面")
        if wait_login_state(timeout):
            log.info("[多账号] 已退出当前账号，回到登录界面")
            return True
        log.info("[多账号] 退出后未识别到登录界面，重试…")
    # 兜底：模板与 OCR 都没命中时，按两次 ESC 退回（避免卡死在标题页）
    log.warning("[多账号] 标题页登出按钮未命中，按两次 ESC 兜底")
    _press("esc")
    time.sleep(1)
    _press("esc")
    return False


def _open_power_menu() -> bool:
    """游戏内：ESC 退回主界面再呼出菜单（菜单以右下角电源图标为标志）。"""
    if not is_game_running():
        log.warning("[多账号] 游戏客户端未运行，无法呼出菜单")
        return False
    for attempt in range(1, 9):
        if _find_img(POWER_BTN) or _find_img(POWER_BTN_ALT):
            log.info("[多账号] 已呼出游戏菜单（检测到电源图标）")
            return True
        if any(_find_img(path) for path in MAIN_ICONS):
            log.info(f"[多账号] 检测到游戏主界面，按 ESC 呼出菜单（第 {attempt} 次）")
        else:
            log.info(f"[多账号] 未识别到主界面图标，按 ESC 退回上一级（第 {attempt} 次）")
        _press("esc")
        time.sleep(1.5)
    return False


def _click_power_to_login(timeout: float = 150) -> bool:
    """点电源 →「是否返回登录页面？」确认 → 登录界面。"""
    if not (_click_img(POWER_BTN) or _click_img(POWER_BTN_ALT)):
        log.warning("[多账号] 未找到菜单里的电源图标")
        return False
    log.info("[多账号] 已点击电源图标，等待「是否返回登录页面」弹窗")
    if not _wait(
        lambda: _find_img(POP_CONFIRM_BTN) or _find_text("是否返回登录页面", crop=REGION_DIALOG), 20
    ):
        log.warning("[多账号] 未出现「是否返回登录页面」弹窗")
        return False
    # 双校验：模板优先，OCR 兜底
    if not _click_img(POP_CONFIRM_BTN):
        _click_text("确认", crop=REGION_CONFIRM)
    log.info("[多账号] 已点击【确认】，等待返回标题界面或登录界面")
    # 只要到达了标题界面（开始游戏）或者账号登录界面，就说明成功脱离游戏主界面了
    return bool(_wait(lambda: detect_state() in (STATE_TITLE, *LOGIN_STATES), timeout))


def logout_to_login_page(timeout: float = GLOBAL_TIMEOUT) -> bool:
    """把当前账号退到登录界面（游戏内 / 标题界面 / 各种登录界面都支持）。

    UNKNOWN（加载中/黑屏）时只等不按：每 2 秒看一次，连续 30 秒识别不到界面才按一次 ESC。
    """
    deadline = time.time() + timeout
    last_state: str | None = None
    unknown_since = time.time()
    while time.time() < deadline:
        state = detect_state()
        if state in LOGIN_STATES:
            log.info(f"[多账号] 已处于登录界面（{STATE_LABELS[state]}）")
            return True
        if state == STATE_UNKNOWN:
            waited = time.time() - unknown_since
            if waited < 10:
                log.debug("[多账号] 界面加载中，等待…")
                time.sleep(1)
                continue
            log.info("[多账号] 10 秒未识别到游戏界面，按一次 ESC 尝试回退")
            _press("esc")
            unknown_since = time.time()
            time.sleep(2)
            continue
        unknown_since = time.time()
        if state != last_state:
            log.info(f"[多账号] {STATE_LABELS.get(state, state)} → 执行登出")
        if state == STATE_CONFIRM_DIALOG:
            _click_img(POP_CONFIRM_BTN) or _click_text("确认", crop=REGION_CONFIRM)
        elif state == STATE_TITLE:
            if not _title_logout():
                # 标题页登出失败：立刻把失败抛给 prepare_account，避免在标题页死循环
                log.warning("[多账号] 标题页登出失败，放弃本轮登出")
                return False
        elif state == STATE_IN_GAME:
            if not _open_power_menu():
                log.info("[多账号] 未能呼出菜单，按 ESC 退回后重试")
                _press("esc")
            elif not _click_power_to_login(min(120, max(30, deadline - time.time()))):
                log.info("[多账号] 电源返回登录页面失败，重试")
                _press("esc")
        else:  # pragma: no cover - 未来状态兜底
            _press("esc")
        last_state = state
        time.sleep(POLL)
    log.error("[多账号] 等待登录界面超时")
    return False


# ------------------------------------------------------------- 登录 SOP
def _open_login_other() -> bool:
    if _click_img(LOGIN_OTHER):
        return True
    return _click_text("登录其他账号", include=True, crop=(0.30, 0.55, 0.70, 0.95))


def _click_login_icon() -> bool:
    """标题页右侧「mi 登入」→ 弹出选账号弹窗。"""
    if _click_img(LOGIN_ICON):
        return True
    return _click_text("登入", crop=REGION_RIGHT)


def _select_password_login() -> bool:
    if _click_img(ACC_PWD_TAB) or _click_img(ACC_PWD_TAB_ALT):
        return True
    return _click_text("账号密码", include=True, crop=(0.30, 0.70, 0.70, 0.99))


def _fill_username(username: str) -> bool:
    if not (
        _click_img(USERNAME_INPUT)
        or _click_img(QQ + "input_phone_email.png")
        or _click_text("输入手机号/邮箱", include=True, crop=REGION_DIALOG)
    ):
        return False
    time.sleep(0.4)
    _clear_and_paste(username)
    return True


def _fill_password(password: str) -> bool:
    if not _click_img(PASSWORD_INPUT):
        # SRA 同样用 Tab 从账号框切到密码框
        _press("tab")
        time.sleep(0.4)
    time.sleep(0.3)
    _clear_and_paste(password)
    return True


def _ensure_agreement() -> bool:
    """检查「同意协议」，未勾选时点圆圈（模板整行，左偏 150px 命中圆圈）。"""
    if not _find_img(AGREE_ROW):
        return True
    if _click_img(AGREE_ROW, offset=(-150, 0)):
        time.sleep(0.6)
        return not _find_img(AGREE_ROW)
    return True


def _submit_login() -> bool:
    return _click_img(ENTER_GAME_BTN) or _click_text("进入游戏", include=True, crop=REGION_DIALOG_BOTTOM)


def _wait_login_result(timeout: float = 120) -> bool:
    """并发等待：欢迎界面 / 开始游戏 / 点击进入 / 月卡弹窗；期间检测账号密码错误。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _find_img(PASSWORD_ERROR) or _find_text("账号或密码错误", include=True, crop=(0.25, 0.05, 0.75, 0.35)):
            raise PasswordError("游戏提示账号或密码错误")
        if _find_img(WELCOME) or _find_img(START_GAME_BTN) or _find_img(CLICK_ENTER_BTN):
            return True
        if _find_img(TRAIN_SUPPLY):
            log.info("[多账号] 检测到月卡弹窗，点击继续")
            _click_screen_center(0.6)
            time.sleep(1.5)
            _click_screen_center(0.8)
            time.sleep(1)
            continue
        time.sleep(POLL)
    return False


def ensure_login_form(timeout: float = GLOBAL_TIMEOUT) -> bool:
    """把界面推进到「账号密码」页：依次处理 登入 → 登录其他账号 → 账号密码。"""
    deadline = time.time() + timeout
    last_state: str | None = None
    unknown_since = time.time()
    while time.time() < deadline:
        state = detect_state()
        if state != STATE_UNKNOWN:
            unknown_since = time.time()
        if state == STATE_PASSWORD_FORM:
            log.info("[多账号] 已进入账号密码页")
            return True
        if state == STATE_UNKNOWN:
            waited = time.time() - unknown_since
            if waited < UNKNOWN_GRACE:
                log.debug("[多账号] 界面加载中，等待…")
                time.sleep(2)
                continue
            log.info(f"[多账号] {UNKNOWN_GRACE} 秒未识别到登录相关界面，按一次 ESC 尝试回退")
            _press("esc")
            unknown_since = time.time()
            time.sleep(STEP_INTERVAL)
            continue
        if state != last_state:
            log.info(f"[多账号] 当前界面：{STATE_LABELS.get(state, state)}")
        if state == STATE_PHONE_FORM:
            log.info("[多账号] 检测到验证码登录页，点击【账号密码】切换")
            if _select_password_login():
                time.sleep(1.5)
                continue
            log.info("[多账号] 未找到【账号密码】入口，重试…")
        elif state == STATE_POPUP:
            log.info("[多账号] 检测到【登录其他账号】，点击中…")
            if _open_login_other():
                time.sleep(1.5)
                continue
            log.info("[多账号] 未找到【登录其他账号】，重试…")
        elif state == STATE_LOGIN_ICON:
            log.info("[多账号] 检测到【登入】按钮，点击中…")
            if _click_login_icon():
                time.sleep(2)
                continue
            log.info("[多账号] 点击【登入】失败，重试…")
        elif state == STATE_TITLE:
            log.info("[多账号] 当前仍是标题界面（已登录其他账号），先登出")
            _title_logout()
        elif state == STATE_IN_GAME:
            log.info("[多账号] 当前在游戏内，先返回登录界面")
            logout_to_login_page(min(120, max(30, deadline - time.time())))
        elif state == STATE_CONFIRM_DIALOG:
            _click_img(POP_CONFIRM_BTN) or _click_text("确认", crop=REGION_CONFIRM)
        else:
            log.info("[多账号] 未识别到登录相关界面，按 ESC 退回后重试")
            _press("esc")
        last_state = state
        time.sleep(STEP_INTERVAL)
    log.error("[多账号] 等待账号密码页超时")
    return False


def login_account(username: str, password: str) -> bool:
    """在登录界面用「账号密码」方式登录；密码错误抛 PasswordError。"""
    if not username or not password:
        log.warning("[多账号] 账号或密码为空，无法自动登录")
        return False

    if not ensure_login_form():
        return False
    log.info("[多账号] 正在输入账号密码…")
    if not _step("填写账号", lambda: _fill_username(username)):
        return False
    if not _step("填写密码", lambda: _fill_password(password)):
        return False
    _ensure_agreement()
    if not _step("点击「进入游戏」", _submit_login):
        return False
    ok = _wait_login_result()
    if ok:
        log.info("[多账号] 登录成功，已回到标题界面")
    return ok


def enter_game(timeout: float = 60) -> bool:
    """标题界面「开始游戏」→「点击进入」→ 游戏内。"""
    if _find_img(START_GAME_BTN) or _find_text("开始游戏", include=True, crop=REGION_TITLE_BOTTOM):
        log.info("[多账号] 点击【开始游戏】")
        if not (_click_img(START_GAME_BTN) or _click_text("开始游戏", include=True, crop=REGION_TITLE_BOTTOM)):
            log.warning("[多账号] 点击【开始游戏】失败")

    deadline = time.time() + timeout
    enter_clicked = False
    last_enter_click = 0.0
    while time.time() < deadline:
        if is_in_game():
            log.info("[多账号] 已进入游戏")
            return True
        if (not enter_clicked or time.time() - last_enter_click > 8) and (
            _find_img(CLICK_ENTER_BTN) or _find_text("点击进入", include=True, crop=REGION_TITLE_BOTTOM)
        ):
            log.info("[多账号] 点击【点击进入】")
            _click_img(CLICK_ENTER_BTN) or _click_text("点击进入", include=True, crop=REGION_TITLE_BOTTOM)
            enter_clicked = True
            last_enter_click = time.time()
        if _find_img(TRAIN_SUPPLY):
            log.info("[多账号] 检测到月卡弹窗，点击继续")
            _click_screen_center(0.6)
            time.sleep(1.5)
            _click_screen_center(0.8)
        time.sleep(POLL)
    log.error("[多账号] 进入游戏超时")
    return False


# ---------------------------------------------------------------- public
def _load_credentials(account: dict):
    from core import account_manager as am

    try:
        return am.load_credential(account["id"])
    except Exception as e:
        log.error(f"[多账号] 读取账号 {account.get('name')} 的凭据失败: {e}")
        return "", ""


def prepare_account(account: dict, timeout: float = 120.0) -> bool:
    """把游戏带到「目标账号可开始任务」的状态（不做 UID 校验，逻辑对齐 SRA）。

    - 勾选「总是重新登录」：先登出当前账号再登录目标账号（只做一次）；
    - 标题页（开始游戏 / 点击进入）：无法确认当前账号，自动登录模式下先退后进；
    - 登录页：填目标账号的账号密码并进入游戏；
    - 已在游戏内且不需要重登：直接返回 True，由调度器执行任务。
    """
    try:
        return _prepare_account_body(account, timeout)
    finally:
        # [MULTI_ACCOUNT] 切号流程退出前释放整帧截图并回收一次内存
        _collect_garbage()


def _prepare_account_body(account: dict, timeout: float = 120.0) -> bool:
    username, password = _load_credentials(account)
    name = account.get("name") or account.get("id")
    auto_login = bool(account.get("auto_login", True))
    need_relogin = bool(account.get("relogin", False))

    if not auto_login and not username:
        log.warning(f"[多账号] 账号 {name} 未开启自动登录且未填写账号，无法自动准备账号")
        return False

    deadline = time.time() + timeout
    unknown_since = time.time()
    last_state: str | None = None
    while time.time() < deadline:
        state = detect_state()
        if state != STATE_UNKNOWN:
            unknown_since = time.time()
            if state != last_state:
                log.info(f"[多账号] 当前界面：{STATE_LABELS.get(state, state)}")
        last_state = state

        if state == STATE_IN_GAME:
            if need_relogin:
                log.info("[多账号] 已勾选「总是重新登录」，强制登出当前账号")
                need_relogin = False
                if not logout_to_login_page():
                    raise SwitchError("登出到登录界面失败")
                continue
            log.info("[多账号] 当前已在游戏内，直接开始执行任务")
            return True

        if state == STATE_TITLE:
            if auto_login or need_relogin:
                log.info("[多账号] 标题页无法确认当前账号，先登出并重新登录")
                need_relogin = False
                if not logout_to_login_page():
                    raise SwitchError("登出到登录界面失败")
            else:
                log.info("[多账号] 未开启自动登录，直接进入游戏")
                return True
            continue

        if state in LOGIN_STATES:
            if not auto_login:
                log.warning("[多账号] 当前在登录页，但未开启自动登录，请手动登录")
                time.sleep(5)
                continue
            log.info(f"[多账号] 开始为账号「{name}」输入账号密码")
            if not login_account(username, password):
                raise SwitchError("登录流程失败")
            need_relogin = False
            if enter_game():
                return True
            log.info("[多账号] 进入游戏未完成，重新识别界面后重试")
            continue

        if state == STATE_CONFIRM_DIALOG:
            _click_img(POP_CONFIRM_BTN) or _click_text("确认", crop=REGION_CONFIRM)
            time.sleep(1)
            continue

        # UNKNOWN：加载中只等待，连续 UNKNOWN_GRACE 秒识别不到界面才按一次 ESC
        if time.time() - unknown_since < UNKNOWN_GRACE:
            log.debug("[多账号] 界面加载中，等待…")
            time.sleep(1)
            continue
        log.info(f"[多账号] {UNKNOWN_GRACE} 秒未识别到游戏界面，按一次 ESC 尝试回退")
        _press("esc")
        unknown_since = time.time()
        time.sleep(2)

    raise SwitchError("等待切号超时")


def switch_to_account(account: dict) -> bool:
    """把游戏切到指定账号并进入游戏（失败返回 False，密码错误抛 PasswordError）。"""
    username, password = _load_credentials(account)
    name = account.get("name") or account.get("id")
    if not username or not password:
        log.warning(f"[多账号] 账号 {name} 未填写登录账号/密码，无法视觉切号")
        return False

    started = time.time()
    try:
        if not ensure_client_running(quiet=True):
            raise SwitchError("启动游戏客户端失败")
        if not prepare_account(account):
            return False
        log.info(f"[多账号] 账号「{name}」切换完成（耗时 {int(time.time() - started)} 秒）")
        return True
    except PasswordError:
        raise
    except SwitchError as e:
        log.error(f"[多账号] 账号 {name} 切号失败: {e}")
        return False
    finally:
        # [MULTI_ACCOUNT] 切号结束清理 auto.screenshot 残留，避免大图长期驻留
        _collect_garbage()


def logout_from_account() -> bool:
    """把一个账号退回到登录界面；失败时关闭客户端（返回 False）。"""
    try:
        if not _controller().is_game_running():
            return True
        if detect_state() in LOGIN_STATES:
            return True
        ok = logout_to_login_page()
        if not ok:
            log.warning("[多账号] 视觉登出失败，关闭客户端")
            close_game_client()
        return ok
    finally:
        # [MULTI_ACCOUNT] 账号收尾后清理截图残留并回收内存
        _collect_garbage()


def switch_game_account(game_uid) -> bool:
    """旧版注册表切号（仅当账号没有填写密码时的兜底方案）。"""
    if not game_uid:
        return True
    try:
        from app.tools.account_manager import load_to_account

        return bool(load_to_account(int(game_uid)))
    except Exception as e:
        log.error(f"[多账号] 切换游戏账户失败 uid={game_uid}: {e}")
        return False
