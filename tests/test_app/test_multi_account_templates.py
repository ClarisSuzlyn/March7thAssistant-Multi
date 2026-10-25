"""视觉切号模板完整性测试：core/logout.py 引用的模板必须都在仓库里。"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE_PREFIX = "./assets/images/screen/"


def _templates() -> dict[str, str]:
    import core.logout as vision

    return {
        name: value
        for name, value in vars(vision).items()
        if isinstance(value, str) and value.startswith(TEMPLATE_PREFIX) and value.endswith(".png")
    }


def _resolve(path: str) -> pathlib.Path:
    return ROOT / path[2:]


def test_templates_are_declared():
    templates = _templates()

    assert len(templates) >= 15, f"模板常量过少，可能被误删: {sorted(templates)}"


def test_templates_exist():
    missing = [f"{name} -> {path}" for name, path in _templates().items() if not _resolve(path).is_file()]

    assert missing == [], "缺少模板文件: " + ", ".join(missing)


def test_templates_are_not_empty():
    empty = [str(_resolve(path)) for path in _templates().values() if _resolve(path).stat().st_size == 0]

    assert empty == []


def test_templates_live_under_multi_account_dir():
    outside = [path for path in _templates().values() if "/multi_account/" not in path]

    assert outside == [], f"模板应统一放在 multi_account/ 下: {outside}"
