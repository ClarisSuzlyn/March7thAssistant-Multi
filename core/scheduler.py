# [MULTI_ACCOUNT] 多账号轮询调度：按顺序切号 → 登录 → 跑任务 → 登出 → 下一个。
"""多账号调度器（在主进程内运行，作为 `main.py multi_account` 任务）。

与原逻辑的关系：
- 每个账号的「任务内容」仍是原项目 `Daily` + `reward`，不复制业务代码；
- 每个账号可以单独设置「任务结束后」行为（沿用程序设置 / 登出当前账号 / 退出游戏 /
  关闭程序 / 无动作），只有「沿用程序设置」才会触发原项目的关机等重操作；
- 中间账号结束必须回到登录页（或关闭客户端），否则无法登录下一个账号；
- 每个账号的任务配置在开跑前由 ``account_manager`` 覆盖到 ``cfg.config``
  并写回 ``./config.yaml``，因此子任务读取到的就是该账号的配置。
"""

from __future__ import annotations

import gc
import time
import traceback

from module.logger import log


def _format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {seconds} 秒"
    return f"{seconds} 秒"


def _notify(message: str):
    try:
        from module.notification import notif

        notif.notify(message)
    except Exception as e:
        log.debug(f"[多账号] 任务通知发送失败: {e}")


def run_multi_account(account_ids=None) -> int:
    from core import account_manager as am
    from core.logout import (
        PasswordError,
        close_game_client,
        ensure_client_running,
        logout_from_account,
        prepare_account,
        switch_game_account,
    )
    from module.config import cfg
    from tasks import game, reward, version
    from tasks.daily.daily import Daily

    accounts = am.list_accounts()
    if account_ids:
        wanted = set(account_ids)
        accounts = [a for a in accounts if a["id"] in wanted]
    if not accounts:
        log.error("[多账号] 没有可执行的账号，请先在「设置 → 多账号」中添加")
        return 1

    total = len(accounts)
    failed = []
    results = []
    started = time.time()
    continue_on_error = am.option("continue_on_error", True)
    auto_logout = am.option("auto_logout", True)
    switch_delay = float(am.option("switch_delay", 3) or 0)

    log.hr(f"多账号轮询开始，共 {total} 个账号", 0)

    # 版本检查与账号无关，只在整轮开始时执行一次；失败直接中止，避免每个账号都卡一次
    try:
        log.hr("多账号：版本检查", 0)
        version.start()
    except Exception as e:
        log.error(f"[多账号] 版本检查失败，已中止: {e}")
        log.error(traceback.format_exc())
        return 1

    last_ok = None
    done_ids = set()

    for index, account in enumerate(accounts):
        name = account.get("name") or account["id"]
        if account["id"] in done_ids:
            log.warning(f"[多账号] 账号 {name} 本轮已完成，跳过重复执行")
            continue
        done_ids.add(account["id"])
        is_last = index == total - 1
        behavior = am.account_after_finish(account)
        log.hr(f"[{index + 1}/{total}] 账号：{name}", 0)
        account_started = time.time()

        try:
            if not am.switch_account(account["id"], cfg):
                failed.append(name)
                continue

            # [MULTI_ACCOUNT] 先确认游戏客户端在运行：没启动就先拉起，再做任何截图/识别
            if not ensure_client_running():
                log.error(f"[多账号] 账号 {name}：游戏客户端启动失败，跳过该账号")
                failed.append(name)
                continue
            log.info("[多账号] 游戏客户端就绪，开始识别当前界面")

            # [MULTI_ACCOUNT] 优先走「图像识别登出→登录→进入游戏」，不依赖注册表
            username, password = am.load_credential(account["id"])
            if account.get("auto_login", True) and username and password:
                log.info("[多账号] 等待游戏进入目标账号…")
                if not prepare_account(account):
                    raise RuntimeError(f"账号 {name} 未能进入可执行任务的状态")
                if not game.ensure_game_ready():
                    raise RuntimeError("切号后未找到游戏窗口")
                log.info("[多账号] 进入游戏完成，开始执行任务")

                # 1. 强行把游戏拉到最前
                game.switch_to_game()
                time.sleep(1)

                # 2. 模拟鼠标点击屏幕正中心，夺回焦点
                try:
                    from module.automation import auto
                    auto.click_point(0.5, 0.5)
                    time.sleep(0.5)
                except Exception:
                    pass

                # 3. 【完美替代方案】模拟按下 Shift 键，静默切换中英文
                try:
                    import pyautogui
                    pyautogui.press('shift')
                    time.sleep(0.2)
                    log.info("[多账号] 已模拟按键修正输入法状态")
                except Exception:
                    pass

                game.start()
            else:
                game_uid = account.get("game_uid") or ""
                if game_uid:
                    log.warning(f"[多账号] 账号未填写登录账号/密码，回退注册表切换 uid={game_uid}")
                    close_game_client()
                    switch_game_account(game_uid)
                else:
                    log.warning("[多账号] 账号未填写登录账号/密码，直接使用当前登录的游戏账号")
                game.start()

            Daily.start()
            reward.start()
            if is_last:
                last_ok = True
            results.append((name, True))
            log.info(f"[多账号] 账号 {name} 任务完成（耗时 {_format_duration(time.time() - account_started)}）")
        except PasswordError as e:
            results.append((name, False))
            failed.append(name)
            log.error(f"[多账号] 账号 {name} 登录失败：{e}，已跳过该账号")
            _notify(f"[多账号] 账号「{name}」登录失败（{e}），已跳过\n请检查「设置 → 多账号」里填写的手机号/邮箱与密码")
            if not continue_on_error:
                break
        except KeyboardInterrupt:
            log.warning("[多账号] 收到中断，停止轮询")
            close_game_client()
            return 130
        except Exception as e:
            results.append((name, False))
            log.error(f"[多账号] 账号 {name} 执行失败（耗时 {_format_duration(time.time() - account_started)}）: {e}")
            log.error(traceback.format_exc())
            failed.append(name)
            if not continue_on_error:
                break

        # 每个账号跑完先把该账号的配置存盘（任务写回的 last_run_timestamp 等状态才会落进
        # 这个账号的 YAML，否则下次轮询会被上一个账号的状态覆盖）
        try:
            am.save_live_config(account["id"])
        except Exception as e:
            log.debug(f"[多账号] 保存账号 {name} 的配置失败: {e}")

        # [MULTI_ACCOUNT] 低频节点强制回收内存：每个账号跑完只做一次，
        # 避免 OpenCV/PIL 的大图矩阵在长时间轮询里堆积（不是高频循环）
        gc.collect()
        log.info("[多账号] 已强制回收内存")

        if is_last:
            if last_ok is None:
                log.warning("[多账号] 最后一个账号未正常结束，跳过任务结束后操作")
            break

        # 非最后一个账号：必须回到登录页（或关闭客户端），绝不能执行 after_finish
        if behavior == "close":
            log.warning(f"[多账号] 账号「{name}」设置为「关闭程序」，提前结束本轮轮询")
            break
        if behavior == "none":
            log.info(f"[多账号] 账号「{name}」设置为「无操作」，跳过登出；下一个账号切号时会自动登出")
        elif behavior == "exit" or not auto_logout:
            log.info(f"[多账号] 账号「{name}」收尾：退出游戏客户端")
            close_game_client()
        else:
            log.info(f"[多账号] 账号「{name}」收尾：登出当前账号")
            logout_from_account()
        if switch_delay > 0:
            time.sleep(switch_delay)

    # 最后一个账号结束：按该账号的「任务结束后」行为收尾
    try:
        am.save_live_config()
    except Exception as e:
        log.warning(f"[多账号] 保存最后一个账号的配置失败: {e}")

    if last_ok:
        behavior = am.account_after_finish(accounts[-1])
        if behavior == "inherit":
            from utils.console import is_gui_started

            if cfg.after_finish == "Loop" and is_gui_started():
                # 图形界面下原项目的循环模式会直接报错退出，多账号交给日志界面的「定时运行」
                log.info("[多账号] 图形界面不支持循环模式，本次轮询结束；需要定时轮询请使用日志界面的「定时运行」")
            else:
                try:
                    game.stop(True)  # 直接交给原项目去退出
                except SystemExit:
                    raise
                except Exception as e:
                    log.error(f"[多账号] 收尾失败: {e}")
                    log.error(traceback.format_exc())
        elif behavior == "logout":
            log.hr("任务结束后：登出当前账号", 0)
            logout_from_account()
        elif behavior == "exit":
            log.hr("任务结束后：退出游戏", 0)
            close_game_client()
        elif behavior == "none":
            log.hr("任务结束后：无操作", 0)
        else:  # close
            log.hr("任务结束后：关闭程序（命令行模式随本任务退出）", 0)

    elapsed = _format_duration(time.time() - started)
    for name, ok in results:
        log.info(f"[多账号] {name}：{'成功' if ok else '失败'}")
    if failed:
        log.hr(f"多账号轮询结束（总耗时 {elapsed}），失败账号：{'、'.join(failed)}", 0)
        return 1
    log.hr(f"多账号轮询结束（总耗时 {elapsed}），全部完成", 0)
    return 0
