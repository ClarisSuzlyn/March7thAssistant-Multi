# [MULTI_ACCOUNT] 原项目更新提醒：只读 GitHub Release 信息，不下载、不安装。
"""检查原项目（moesnow/March7thAssistant）是否有新版本。"""

from __future__ import annotations

import json
import re
import urllib.request

RELEASE_API = "https://api.github.com/repos/moesnow/March7thAssistant/releases/latest"
USER_AGENT = "March7thAssistant-MultiAccount"


def _parse_version(version: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(version or ""))
    return tuple(int(value) for value in numbers[:4]) or (0,)


def fetch_latest(timeout: float = 8.0) -> tuple[str, str] | None:
    """返回 (tag, html_url)，网络不可用时返回 None。"""
    request = urllib.request.Request(RELEASE_API, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    tag = str(payload.get("tag_name") or "").strip()
    url = str(payload.get("html_url") or "").strip()
    if not tag:
        return None
    return tag, url


def check_latest(current_version: str, timeout: float = 8.0) -> tuple[str, str] | None:
    """当前版本落后于最新 Release 时返回 (tag, url)，否则 None。"""
    try:
        latest = fetch_latest(timeout)
    except Exception:
        return None
    if latest is None:
        return None
    tag, url = latest
    if _parse_version(tag) > _parse_version(current_version):
        return tag, url
    return None
