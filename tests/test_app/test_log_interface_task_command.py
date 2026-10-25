"""[MULTI_ACCOUNT] 内置任务命令行拆分测试（支持带参数的任务，如 multi_account --account xxx）。"""

import sys

import pytest

if sys.platform != 'win32':
    pytest.skip('GUI 测试仅在 Windows 平台运行', allow_module_level=True)

from app.log_interface import LogInterface, _split_task_command
from utils.tasks import PAUSABLE_TASKS


def test_split_plain_task():
    assert _split_task_command('main') == ['main']


def test_split_task_with_account_argument():
    assert _split_task_command('multi_account --account abc-123') == [
        'multi_account',
        '--account',
        'abc-123',
    ]


def test_split_quoted_argument_with_spaces():
    assert _split_task_command('multi_account --account "a b"') == [
        'multi_account',
        '--account',
        'a b',
    ]


def test_split_keeps_windows_backslashes():
    assert _split_task_command(r'workflow "C:\Program Files\game.exe"') == [
        'workflow',
        r'C:\Program Files\game.exe',
    ]


def test_split_empty_returns_empty_list():
    assert _split_task_command('   ') == []


def test_multi_account_is_pausable_task():
    assert 'multi_account' in PAUSABLE_TASKS


def test_resolve_pause_support_uses_first_token():
    # 带参数的调用也要按首个 token 判定是否需要暂停控制
    assert LogInterface._resolvePauseSupport('multi_account --account abc') is True
    assert LogInterface._resolvePauseSupport('main') is True
    assert LogInterface._resolvePauseSupport('bettergi.exe run') is False
