"""软刷新（SettingInterface.refresh_values）测试：只改控件显示值，不触发保存、不重建界面。"""
import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QSlider, QWidget


@pytest.fixture()
def setting_interface(qapp):
    from app.setting_interface import SettingInterface

    return SettingInterface


def _card(parent, configname):
    card = QWidget(parent)
    card.configname = configname
    return card


def _make_combo(parent, items, current=0):
    combo = QComboBox(parent)
    for data, text in items:
        combo.addItem(text, userData=data)
    combo.setCurrentIndex(current)
    return combo


def test_refresh_updates_combo_without_emitting(setting_interface, monkeypatch, qapp):
    from app.setting_interface import cfg

    host = QWidget()
    card = _card(host, "game_channel")
    combo = _make_combo(host, [("cn", "官服"), ("bl", "B服")])
    card.comboBox = combo
    emitted = []
    combo.currentIndexChanged.connect(lambda index: emitted.append(index))
    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: "bl")

    updated = setting_interface.refresh_values(host)

    assert updated == 1
    assert combo.currentIndex() == 1
    assert emitted == [], "软刷新不能触发 currentIndexChanged（否则会写盘/重载）"


def test_refresh_updates_switch_button_and_text(setting_interface, monkeypatch, qapp):
    from qfluentwidgets import SwitchButton

    from app.setting_interface import cfg

    host = QWidget()
    card = _card(host, "fight_enable")
    switch = SwitchButton(host)
    card.switchButton = switch
    values = []
    card.setValue = values.append
    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: False)

    assert setting_interface.refresh_values(host) == 1
    assert values == [False], "应走卡片自身的 setValue(False) 以同步开/关文字"
    assert not switch.isChecked()


def test_refresh_updates_slider_and_value_label(setting_interface, monkeypatch, qapp):
    from app.setting_interface import cfg

    host = QWidget()
    card = _card(host, "power_plan")
    slider = QSlider(host)
    slider.setRange(0, 10)
    card.slider = slider
    label = QLabel(host)
    card.valueLabel = label
    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: 7)

    assert setting_interface.refresh_values(host) == 1
    assert slider.value() == 7
    assert label.text() == "7"


def test_refresh_updates_switch_and_secondary_combo(setting_interface, monkeypatch, qapp):
    """同时带开关和下拉框的卡片（如组队/沉浸器）两处都要刷新。"""
    from qfluentwidgets import SwitchButton

    from app.setting_interface import cfg

    host = QWidget()
    card = _card(host, "team_enable")
    card.configname2 = "team_count"
    switch = SwitchButton(host)
    card.switchButton = switch
    combo = _make_combo(host, [("1", "1"), ("2", "2"), ("4", "4")])
    card.comboBox = combo
    values = []
    card.setValue = values.append

    table = {"team_enable": False, "team_count": "4"}
    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: table[key])

    assert setting_interface.refresh_values(host) == 1
    assert values == [False]
    assert combo.currentIndex() == 2


def test_refresh_updates_real_cards_without_saving(setting_interface, monkeypatch, qapp):
    """用原项目真实卡片验证：软刷新能改值，且不会触发 cfg.set_value。"""
    from qfluentwidgets import FluentIcon as FIF

    from app.card.comboboxsettingcard1 import ComboBoxSettingCard1
    from app.card.switchsettingcard1 import SwitchSettingCard1
    from app.setting_interface import cfg

    table = {"game_channel": "cn", "power_enable": True}
    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: table.get(key, default))

    host = QWidget()
    switch_card = SwitchSettingCard1(
        FIF.POWER_BUTTON, "开关", configname="power_enable", parent=host
    )
    combo_card = ComboBoxSettingCard1(
        "game_channel", FIF.GLOBE, "渠道", texts=["cn", "bl"], parent=host
    )
    assert switch_card.switchButton.isChecked() is True
    assert combo_card.comboBox.currentIndex() == 0

    table.update({"power_enable": False, "game_channel": "bl"})

    def _boom(*args, **kwargs):
        raise AssertionError("软刷新不应调用 cfg.set_value()")

    monkeypatch.setattr(cfg, "set_value", _boom)

    assert setting_interface.refresh_values(host) == 2
    assert switch_card.switchButton.isChecked() is False
    assert combo_card.comboBox.currentIndex() == 1


def test_refresh_never_writes_config(setting_interface, monkeypatch, qapp):
    from app.setting_interface import cfg

    host = QWidget()
    card = _card(host, "power_enable")
    combo = _make_combo(host, [("True", "开"), ("False", "关")], current=0)
    card.comboBox = combo

    def _boom(*args, **kwargs):
        raise AssertionError("软刷新不应调用 cfg.set_value()")

    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: "False")
    monkeypatch.setattr(cfg, "set_value", _boom)

    assert setting_interface.refresh_values(host) == 1
    assert combo.currentIndex() == 1


def test_refresh_skips_unknown_combobox_value(setting_interface, monkeypatch, qapp):
    from app.setting_interface import cfg

    host = QWidget()
    card = _card(host, "game_channel")
    combo = _make_combo(host, [("cn", "官服")], current=0)
    card.comboBox = combo
    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: "not-a-value")

    assert setting_interface.refresh_values(host) == 0
    assert combo.currentIndex() == 0


def test_refresh_ignores_widgets_without_configname(setting_interface, monkeypatch, qapp):
    from app.setting_interface import cfg

    host = QWidget()
    _card(host, "")
    panel = QWidget(host)
    panel.configname = None
    monkeypatch.setattr(cfg, "get_value", lambda key, default=None: "x")

    assert setting_interface.refresh_values(host) == 0
