# [MULTI_ACCOUNT] 内存黑匣子：长时间多账号轮询时记录 StarRail.exe 内存，闪退时保留现场。
"""内存黑匣子。

- 守护线程每 10 秒采样 ``StarRail.exe`` 的内存占用（RSS 求和），保留最近 1 小时；
- 调度器只在「任务执行期间」调用 :func:`arm` / :func:`disarm`，避免切号、登出时把
  正常关闭客户端误判成闪退；
- 闪退时把最后的内存记录和程序日志尾部写到 ``settings/multi_account/crash_logs/``。
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime

from module.logger import log

CRASH_DIR = os.path.join("settings", "multi_account", "crash_logs")
SAMPLE_INTERVAL = 10
MAX_SAMPLES = 360  # 10 秒一条 ≈ 1 小时
PROCESS_NAME = "StarRail.exe"


def _read_memory() -> tuple[bool, float]:
    """返回 (进程是否在运行, 内存总和 MB)。"""
    try:
        import psutil
    except Exception:
        return False, 0.0
    found = False
    total = 0
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == PROCESS_NAME.lower():
                found = True
                total += proc.memory_info().rss
        except Exception:
            continue
    return found, total / (1024 * 1024)


def _tail_log(max_lines: int = 200) -> str:
    try:
        files = [os.path.join("logs", name) for name in os.listdir("logs") if name.endswith(".log")]
        if not files:
            return "# 没有找到日志文件\n"
        newest = max(files, key=os.path.getmtime)
        with open(newest, "r", encoding="utf-8", errors="replace") as file:
            lines = file.readlines()
        return f"# 日志文件：{newest}（最后 {max_lines} 行）\n" + "".join(lines[-max_lines:])
    except Exception as e:
        return f"# 读取日志失败：{e}\n"


class MemoryMonitor:
    """单例式内存采样器（线程安全，可重复 start/stop）。"""

    def __init__(self):
        self._samples: deque[tuple[float, float]] = deque(maxlen=MAX_SAMPLES)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._armed = False
        self._was_running = False
        self._last_crash_path: str | None = None

    # ------------------------------------------------------------ lifecycle
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="multi-account-memory", daemon=True)
        self._thread.start()
        log.info(f"[多账号] 内存黑匣子已启动（每 {SAMPLE_INTERVAL} 秒采样 {PROCESS_NAME}）")
        return True

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        log.info("[多账号] 内存黑匣子已停止")

    def arm(self):
        """任务开始：从这一刻起进程消失视为闪退。"""
        self._armed = True
        self._was_running = _read_memory()[0]
        self._record(force=True)

    def disarm(self):
        self._armed = False

    # ------------------------------------------------------------- sampling
    def _record(self, force: bool = False) -> None:
        running, memory = _read_memory()
        with self._lock:
            self._samples.append((time.time(), memory if running else 0.0))
        if not running and self._armed and self._was_running and self._last_crash_path is None:
            self._dump_crash("游戏进程在任务执行期间消失（疑似闪退）")
        self._was_running = running

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._record()
            except Exception as e:
                log.debug(f"[多账号] 内存采样失败: {e}")
            self._stop.wait(SAMPLE_INTERVAL)

    def poll(self) -> None:
        """外部主动采样一次（调度器每个账号收尾时调用）。"""
        self._record()

    # --------------------------------------------------------------- crash
    def _dump_crash(self, reason: str) -> str | None:
        try:
            os.makedirs(CRASH_DIR, exist_ok=True)
            path = os.path.join(CRASH_DIR, f"crash_{datetime.now():%Y%m%d_%H%M%S}.log")
            with self._lock:
                samples = list(self._samples)
            lines = [
                f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
                f"原因：{reason}",
                f"进程：{PROCESS_NAME}",
                "",
                "# 最近内存记录（时间 / MB）",
            ]
            for ts, memory in samples[-120:]:
                lines.append(f"{datetime.fromtimestamp(ts):%H:%M:%S}\t{memory:.1f} MB")
            peak = max((memory for _, memory in samples), default=0.0)
            lines.append(f"\n峰值内存：{peak:.1f} MB")
            lines.append("")
            lines.append(_tail_log())
            with open(path, "w", encoding="utf-8") as file:
                file.write("\n".join(lines))
            self._last_crash_path = path
            log.warning(f"[多账号] 检测到游戏闪退，已保存内存黑匣子：{path}")
            return path
        except Exception as e:
            log.error(f"[多账号] 保存内存黑匣子失败: {e}")
            return None

    def snapshot(self) -> list[tuple[float, float]]:
        with self._lock:
            return list(self._samples)


_memory_monitor: MemoryMonitor | None = None


def get_memory_monitor() -> MemoryMonitor:
    global _memory_monitor
    if _memory_monitor is None:
        _memory_monitor = MemoryMonitor()
    return _memory_monitor
