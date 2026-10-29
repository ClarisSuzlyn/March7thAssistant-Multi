# [MULTI_ACCOUNT] 多账号设置页：账号切换、账号配置、任务结束、启动入口。
"""多账号设置页。

- 账号配置分组：账号名称、渠道服、游戏路径（可独立）、登录账号、登录密码、自动登录；
- 任务结束分组：该账号任务跑完后做什么（沿用程序设置 / 登出 / 退出游戏 / 关闭程序 / 无动作）；
- 顶部：新增、删除、切换账号、保存配置、启动当前账号配置（下拉含启动所有账号 / 仅保存配置）。

所有修改只在点击「保存配置」（或启动前）写入当前账号的 YAML；切换账号不再自动保存。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QWidget,
)
from qfluentwidgets import (
    Action,
    CheckBox,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PrimarySplitPushButton,
    PushButton,
    RoundMenu,
    SettingCard,
    SettingCardGroup,
    SwitchButton,
    ToolButton,
    TransparentPushButton,
    TransparentToolButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from app.common.signal_bus import signalBus
from core import account_manager as am
from module.config import cfg
from module.localization import tr


def _account_channels():
    return ((tr("官服"), 0), (tr("B服"), 1))


def _after_finish_items():
    return (
        ("inherit", tr("跟随程序设置")),
        ("logout", tr("登出当前账号")),
        ("exit", tr("退出游戏（关闭客户端）")),
        ("close", tr("关闭程序")),
        ("none", tr("无动作")),
    )


class _PasswordBox(QWidget):
    """带显示/隐藏按钮的密码输入框（不用 PasswordLineEdit，保证眼睛可点）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit = LineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(tr("登录密码"))
        self.toggleButton = TransparentToolButton(FIF.VIEW, self)
        self.toggleButton.setToolTip(tr("显示/隐藏密码"))
        self.toggleButton.clicked.connect(self._toggle_visible)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.toggleButton, 0)
        self.setFixedHeight(33)

    def _toggle_visible(self):
        password = self.edit.echoMode() == QLineEdit.EchoMode.Password
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if password else QLineEdit.EchoMode.Password)
        self.toggleButton.setIcon(FIF.HIDE if password else FIF.VIEW)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str):
        self.edit.setText(text)

    def reset_to_password_mode(self):
        """【新增】强制恢复为密码隐藏模式，并重置眼睛图标"""
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggleButton.setIcon(FIF.VIEW)

def mask_text(text, is_email=False):
    """对手机号或邮箱进行脱敏处理"""
    if not text or len(text) < 5:
        return text
    if is_email and "@" in text:
        prefix, domain = text.split("@", 1)
        if len(prefix) <= 3:
            return f"{prefix[0]}***@{domain}"
        return f"{prefix[:3]}***@{domain}"
    # 默认按手机号处理
    return f"{text[:3]}******{text[-2:]}"


class MultiAccountGroup(SettingCardGroup):
    """设置页里的「多账号」分组。"""

    def __init__(self, parent=None):
        super().__init__(tr("多账号"), parent)
        self.account_ids: list[str] = []
        self._loading = False
        self._loaded: dict | None = None
        self.channels = _account_channels()
        self.after_finish_items = _after_finish_items()

        self._build_top_bar()
        self._build_account_group()
        self._build_after_finish_group()

        self.tipLabel = QLabel(
            tr(
                "账号配置保存在 settings/multi_account/；切换账号会用该账号的配置覆盖当前配置，"
                "点击「保存配置」才会写回当前账号。"
            ),
            self,
        )
        self.tipLabel.setWordWrap(True)
        self.tipLabel.setMinimumHeight(44)
        self.tipLabel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.tipLabel.setObjectName("tipLabel")
        self.tipLabel.setStyleSheet("color: rgba(128, 128, 128, 180);")
        self.vBoxLayout.addWidget(self.tipLabel)

        self._reload_from_store()

    # ---------------------------------------------------------------- UI
    def _build_top_bar(self):
        self.topBar = QWidget(self)
        layout = QHBoxLayout(self.topBar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        icon_size = QSize(16, 16)
        self.addButton = ToolButton(FIF.ADD, self.topBar)
        self.addButton.setToolTip(tr("新增账号"))
        self.addButton.setIconSize(icon_size)
        self.addButton.clicked.connect(self._on_add)

        self.copyButton = ToolButton(FIF.COPY, self.topBar)
        self.copyButton.setToolTip(tr("复制当前账号配置"))
        self.copyButton.setIconSize(icon_size)
        self.copyButton.clicked.connect(self._on_copy_account)

        self.removeButton = ToolButton(FIF.REMOVE, self.topBar)
        self.removeButton.setToolTip(tr("删除当前账号"))
        self.removeButton.setIconSize(icon_size)
        self.removeButton.clicked.connect(self._on_remove)

        self.accountCombo = ComboBox(self.topBar)
        self.accountCombo.setPlaceholderText(tr("暂无账号"))
        self.accountCombo.setMinimumWidth(150)
        self.accountCombo.currentIndexChanged.connect(self._on_account_changed)

        self.saveButton = PushButton(tr("保存配置"), self.topBar, FIF.SAVE)
        self.saveButton.setToolTip(tr("保存当前账号配置"))
        self.saveButton.clicked.connect(lambda: self._on_save(silent=False))

        self.startButton = PrimarySplitPushButton(tr("启动当前配置"), self.topBar)
        self.startButton.setIcon(FIF.PLAY)
        self.startButton.clicked.connect(self._on_start_current)

        menu = RoundMenu(parent=self.startButton)
        menu.addAction(Action(FIF.PLAY, tr("启动当前账号配置"), triggered=self._on_start_current))
        menu.addAction(Action(FIF.PLAY, tr("启动所有账号配置"), triggered=self._on_start_all))
        menu.addAction(Action(FIF.SAVE, tr("仅保存配置"), triggered=lambda: self._on_save(silent=False)))
        self.startButton.setFlyout(menu)

        layout.addWidget(self.addButton)
        layout.addWidget(self.copyButton)
        layout.addWidget(self.removeButton)
        layout.addSpacing(15) # 账号操作组和下拉框的间距
        layout.addWidget(self.accountCombo)
        layout.addWidget(self.saveButton)
        # layout.addSpacing(60)  # 把间距拉大，隔开保存和启动
        layout.addSpacing(10)
        divider = QLabel("|", self.topBar)
        divider.setStyleSheet("color: rgba(128, 128, 128, 100); font-size: 16px;") # 半透明灰色竖线
        layout.addWidget(divider)
        layout.addSpacing(10)
        layout.addWidget(self.startButton)

        # [MULTI_ACCOUNT] 顶部操作栏由 SettingInterface 领养并悬浮在「设置」标题右侧，
        # 这里不放进本分组的布局，也不参与滚动。
        self.topBar.hide()

    def adopt_top_bar(self, host: QWidget):
        """把顶部操作栏交给外部容器（SettingInterface）承载，悬浮显示不随页面滚动。"""
        self.topBar.setParent(host)
        self.topBar.setObjectName("accountTopBar")
        self.topBar.show()
        self.topBar.raise_()
        return self.topBar

    def _build_account_group(self):
        self.accountGroup = SettingCardGroup(tr("账号配置"), self)

        self.nameEdit = LineEdit(self)
        self.nameEdit.setPlaceholderText(tr("账号名称"))
        self.nameEdit.setFixedWidth(260)
        self.accountGroup.addSettingCard(
            self._input_card(FIF.EDIT, tr("账号名称"), tr("仅用于界面区分，可随时修改"), self.nameEdit)
        )

        self.channelCombo = ComboBox(self)
        self.channelCombo.setFixedWidth(200)
        for label, _ in self.channels:
            self.channelCombo.addItem(label)
        self.accountGroup.addSettingCard(
            self._input_card(FIF.GLOBE, tr("渠道服"), tr("官服 / B服（暂未开放）"), self.channelCombo)
        )

        pathWidget = QWidget(self)
        pathLayout = QHBoxLayout(pathWidget)
        pathLayout.setContentsMargins(0, 0, 0, 0)
        pathLayout.setSpacing(6)
        self.pathSwitch = SwitchButton(pathWidget)
        self.pathSwitch.setOnText(tr("全局"))
        self.pathSwitch.setOffText(tr("独立"))
        self.pathSwitch.checkedChanged.connect(self._on_path_switch_changed)
        self.pathEdit = LineEdit(pathWidget)
        self.pathEdit.setPlaceholderText(tr("选择独立游戏路径"))
        self.pathEdit.setMinimumWidth(220)
        self.browseButton = ToolButton(FIF.FOLDER, pathWidget)
        self.browseButton.clicked.connect(self._on_browse_path)
        pathLayout.addWidget(self.pathSwitch)
        pathLayout.addWidget(self.pathEdit, 1)
        pathLayout.addWidget(self.browseButton)
        pathWidget.setFixedWidth(380)
        self.accountGroup.addSettingCard(
            self._input_card(FIF.FOLDER, tr("游戏路径"), tr("开启开关后跟随「程序设置」中的全局路径"), pathWidget)
        )

        self.userEdit = LineEdit(self)
        self.userEdit.setPlaceholderText(tr("手机号或邮箱"))
        self.userEdit.setFixedWidth(260)

        # ================= 【新增焦点事件处理】 =================
        def on_focus_in(event):
            # 1. 聚焦时：恢复明文
            real_text = getattr(self.userEdit, '_real_text', '')
            if real_text:
                self.userEdit.setText(real_text)
            # 调用父类默认处理（保证光标正常显示）
            QWidget.focusInEvent(self.userEdit, event)

        def on_focus_out(event):
            # 2. 失焦时：保存最新修改的文本，然后脱敏
            current_text = self.userEdit.text().strip()
            if current_text and "*" not in current_text:
                self.userEdit._real_text = current_text  # 更新真实值

            real_text = getattr(self.userEdit, '_real_text', '')
            if real_text:
                is_mail = "@" in real_text
                self.userEdit.setText(mask_text(real_text, is_mail))
            # 调用父类默认处理
            QWidget.focusOutEvent(self.userEdit, event)

        # 绑定事件
        self.userEdit.focusInEvent = on_focus_in
        self.userEdit.focusOutEvent = on_focus_out
        # ========================================================

        self.accountGroup.addSettingCard(
            self._input_card(FIF.PEOPLE, tr("登录账号"), tr("游戏登录用的手机号或邮箱"), self.userEdit)
        )

        self.pwdInput = _PasswordBox(self)
        self.pwdInput.setFixedWidth(260)
        self.accountGroup.addSettingCard(
            self._input_card(FIF.VPN, tr("登录密码"), tr("使用 Windows DPAPI 加密保存在本机"), self.pwdInput)
        )

        self.autoLoginBox = CheckBox(tr("启用"), self)
        self.autoLoginBox.setChecked(True)
        self.autoLoginBox.setMinimumWidth(60)
        self.accountGroup.addSettingCard(
            self._input_card(FIF.SYNC, tr("自动登录"), tr("切号时自动填写账号密码"), self.autoLoginBox)
        )

        self.reloginBox = CheckBox(tr("启用"), self)
        self.reloginBox.setMinimumWidth(60)
        self.accountGroup.addSettingCard(
            self._input_card(FIF.VIEW, tr("总是重新登录"), tr("即使当前已是同一账号也重新登录一次"), self.reloginBox)
        )

        self.vBoxLayout.addWidget(self.accountGroup)

    def _build_after_finish_group(self):
        self.afterFinishGroup = SettingCardGroup(tr("任务结束"), self)
        self.afterFinishCombo = ComboBox(self)
        self.afterFinishCombo.setFixedWidth(200)
        for value, label in self.after_finish_items:
            self.afterFinishCombo.addItem(label, userData=value)
        self.afterFinishGroup.addSettingCard(
            self._input_card(
                FIF.POWER_BUTTON,
                tr("任务结束后"),
                tr("该账号跑完后的收尾动作（多账号轮询时中间账号只切号，不会关机）"),
                self.afterFinishCombo,
            )
        )
        self.vBoxLayout.addWidget(self.afterFinishGroup)

    def _input_card(self, icon, title: str, content: str, widget: QWidget) -> SettingCard:
        card = SettingCard(icon, title, content, self)
        if not isinstance(widget, (LineEdit, CheckBox)):
            widget.setFixedHeight(33)
        card.hBoxLayout.addSpacing(8)
        card.hBoxLayout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(8)
        return card

    # ------------------------------------------------------------- 数据
    @staticmethod
    def _snapshot(account: dict) -> dict:
        return {
            "name": account.get("name") or "",
            "channel": am.normalize_channel(account.get("channel", 0)),
            "use_global_path": bool(account.get("use_global_path", True)),
            "game_path": account.get("game_path") or "",
            "auto_login": bool(account.get("auto_login", True)),
            "relogin": bool(account.get("relogin", False)),
            "after_finish": account.get("after_finish") or "inherit",
        }

    def _widgets_snapshot(self) -> dict:
        return {
            "name": self.nameEdit.text().strip(),
            "channel": self.channelCombo.currentIndex(),
            "use_global_path": self.pathSwitch.isChecked(),
            "game_path": self.pathEdit.text().strip(),
            "auto_login": self.autoLoginBox.isChecked(),
            "relogin": self.reloginBox.isChecked(),
            "after_finish": self.afterFinishCombo.currentData() or "inherit",
        }

    def _is_dirty(self) -> bool:
        if self._loaded is None:
            return False
        loaded = dict(self._loaded)
        username, password = am.load_credential(am.current_id()) if am.current_id() else ("", "")
        loaded["credential"] = (username or "", password or "")

        current = self._widgets_snapshot()

        # ===== 【修复】获取真实的当前账号，忽略脱敏星号 =====
        current_text = self.userEdit.text().strip()
        if "*" in current_text:
            current_username = getattr(self.userEdit, '_real_text', "")
        else:
            current_username = current_text

        current["credential"] = (current_username, self.pwdInput.text())
        return loaded != current

    def _current_account(self) -> dict | None:
        idx = self.accountCombo.currentIndex()
        if idx < 0 or idx >= len(self.account_ids):
            return None
        return am.get_account(self.account_ids[idx])

    def _reload_from_store(self):
        accounts = am.list_accounts()
        self.account_ids = [account["id"] for account in accounts]
        self._loading = True
        try:
            self.accountCombo.clear()
            for account in accounts:
                self.accountCombo.addItem(account.get("name") or account["id"])
            current = am.current_id()
            index = self.account_ids.index(current) if current in self.account_ids else (0 if accounts else -1)
            self.accountCombo.setCurrentIndex(index)
        finally:
            self._loading = False

        has_account = bool(accounts)
        for widget in (self.copyButton, self.removeButton, self.saveButton, self.startButton):
            widget.setEnabled(has_account)
        self._load_account(self._current_account())

    def _load_account(self, account: dict | None):
        self._loading = True
        try:
            if account is None:
                self.nameEdit.clear()
                self.channelCombo.setCurrentIndex(0)
                self.pathSwitch.setChecked(True)
                self.pathEdit.clear()
                self.userEdit.clear()
                self.pwdInput.setText("")
                self.autoLoginBox.setChecked(True)
                self.reloginBox.setChecked(False)
                self.afterFinishCombo.setCurrentIndex(0)
                self._loaded = None
                return
            snapshot = self._snapshot(account)
            self.nameEdit.setText(snapshot["name"])
            self.channelCombo.setCurrentIndex(max(0, min(len(self.channels) - 1, snapshot["channel"])))
            self.pathSwitch.setChecked(snapshot["use_global_path"])
            self.pathEdit.setText(snapshot["game_path"])
            self.pathEdit.setEnabled(not snapshot["use_global_path"])
            self.browseButton.setEnabled(not snapshot["use_global_path"])
            username, password = am.load_credential(account["id"])
            # 【修改开始】加载时显示脱敏账号，并暗存真实账号
            if username:
                is_mail = "@" in username
                self.userEdit.setText(mask_text(username, is_mail))
                self.userEdit._real_text = username  # 把真实值绑在控件上
            else:
                self.userEdit.setText("")
                self.userEdit._real_text = ""
            # 【修改结束】

            self.pwdInput.setText(password or "")
            self.pwdInput.reset_to_password_mode()  # 【新增】切号时强制恢复为密文状态
            self.autoLoginBox.setChecked(snapshot["auto_login"])
            self.reloginBox.setChecked(snapshot["relogin"])
            index = self.afterFinishCombo.findData(snapshot["after_finish"])
            self.afterFinishCombo.setCurrentIndex(max(index, 0))
            self._loaded = dict(snapshot)
            self._loaded["credential"] = (username or "", password or "")
        finally:
            self._loading = False

    def _on_path_switch_changed(self, checked: bool):
        self.pathEdit.setEnabled(not checked)
        self.browseButton.setEnabled(not checked)

    # ------------------------------------------------------------- 动作
    def _toast(self, title: str, content: str, success: bool = True):
        info = InfoBar.success if success else InfoBar.warning
        info(title, content, parent=self.window() or self, duration=2500, position=InfoBarPosition.TOP)

    def _on_add(self):
        import traceback

        from PySide6.QtWidgets import QInputDialog

        from module.logger import log

        try:
            if am.list_accounts() and self._is_dirty():
                self._confirm_save()

            # 修复：default_account_name 不需要传参
            default_name = am.default_account_name()
            name, ok = QInputDialog.getText(self, tr("新增账号"), tr("请输入账号名称"), text=default_name)

            if not ok:
                return

            final_name = name.strip() if name.strip() else default_name

            # ===== 新增校验 =====
            if self._is_name_duplicated(final_name):
                self._toast(tr("新增失败"), tr("账号名称「{name}」已存在，请使用其他名称").format(name=final_name), success=False)
                return
            # ====================

            account = am.new_account(final_name)

            self._reload_from_store()
            index = self.account_ids.index(account["id"]) if account["id"] in self.account_ids else -1
            if index >= 0:
                self.accountCombo.setCurrentIndex(index)
            self._toast(tr("已新增账号"), tr("请填写登录账号与密码后点击「保存配置」"))

        except Exception as e:
            log.error(f"[多账号] 新增账号失败: {e}\n{traceback.format_exc()}")
            self._toast(tr("新增失败"), str(e), success=False)

    def _on_copy_account(self):
        import traceback

        from PySide6.QtWidgets import QInputDialog

        from module.logger import log

        source = self._current_account()
        if source is None:
            self._toast(tr("复制失败"), tr("当前没有可复制的账号"), success=False)
            return

        try:
            if am.list_accounts() and self._is_dirty():
                self._confirm_save()

            default_name = f"{source.get('name') or source['id']} " + tr("副本")
            name, ok = QInputDialog.getText(self, tr("复制账号"), tr("请输入新账号名称"), text=default_name)
            if not ok:
                return

            final_name = name.strip() if name.strip() else default_name
            if self._is_name_duplicated(final_name):
                self._toast(tr("复制失败"), tr("账号名称「{name}」已存在，请使用其他名称").format(name=final_name), success=False)
                return

            account = am.copy_account(source["id"], final_name)
            if account is None:
                self._toast(tr("复制失败"), tr("源账号不存在"), success=False)
                return

            self._reload_from_store()
            index = self.account_ids.index(account["id"]) if account["id"] in self.account_ids else -1
            if index >= 0:
                self.accountCombo.setCurrentIndex(index)
            self._toast(tr("已复制账号"), tr("已复制「{name}」的账号配置").format(name=source.get("name") or source["id"]))
        except Exception as e:
            log.error(f"[多账号] 复制账号失败: {e}\n{traceback.format_exc()}")
            self._toast(tr("复制失败"), str(e), success=False)

    def _on_remove(self):
        account = self._current_account()
        if account is None:
            return
        import traceback

        from PySide6.QtWidgets import QMessageBox

        from module.logger import log

        answer = QMessageBox.question(
            self,
            tr("删除账号"),
            tr("确定删除账号「{name}」吗？其配置会被一并删除。").format(name=account.get("name") or account["id"]),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            am.remove_account(account["id"])

            # ===== 修复：不要调用 switch_account（它会保存旧数据），直接 apply！ =====
            # 解决当前账号删除后第一个账号被幽灵账号的配置cfg给夺舍的问题，以及考虑所有账号都删除后的配置情况
            new_current = am.current_id()
            if new_current:
                am.apply_account(new_current, cfg)
                self._reload_settings_ui()
            else:
                # 【新增】如果所有账号都被删光了，强制把内存里的任务配置重置回默认，防止下次新建继承幽灵数据
                import copy

                # 简单粗暴的写法：把 cfg 里的私有任务键全删除/重置
                for key in list(cfg.config.keys()):
                    if am.is_private_key(key):
                        del cfg.config[key]
                # 然后重新加载默认模板
                default_config = am._default_config()
                for k, v in default_config.items():
                    if am.is_private_key(k):
                        cfg.config[k] = copy.deepcopy(v)
                cfg.save_config()
                self._reload_settings_ui()
            # ==========================================================

            self._reload_from_store()
            self._toast(tr("已删除账号"), "")
        except Exception as e:
            log.error(f"[多账号] 删除账号失败: {e}\n{traceback.format_exc()}")
            self._toast(tr("删除失败"), str(e), success=False)

    def _on_account_changed(self, index: int):
        if self._loading or index < 0 or index >= len(self.account_ids):
            return
        target_id = self.account_ids[index]
        if target_id == am.current_id():
            self._load_account(am.get_account(target_id))
            return
        if self._is_dirty():
            self._confirm_save()
        try:
            am.switch_account(target_id, cfg)
        except Exception as e:
            self._toast(tr("切换失败"), str(e), success=False)
            self._reload_from_store()  # 下拉框回到真正生效的账号，避免界面和配置对不上
            return
        self._reload_from_store()
        self._reload_settings_ui()

    def _reload_settings_ui(self):
        """[MULTI_ACCOUNT] 切号后软刷新原项目设置页。

        原项目卡片只在构建时读一次配置；这里直接按 cfg 里的新值刷新现有卡片
        （只改控件显示值，不销毁/重建控件，也不弹任何提示），切换是瞬时的。
        """
        window = self.window()
        setting = getattr(window, "settingInterface", None)
        refresh = getattr(setting, "refresh_values", None)
        if callable(refresh):
            refresh()

    def _confirm_save(self):
        message = MessageBox(tr("未保存的修改"), tr("当前账号有未保存的修改，是否先保存？"), self.window())
        message.yesButton.setText(tr("保存"))
        message.cancelButton.setText(tr("不保存"))
        if message.exec():
            self._on_save(silent=True)

    def _on_browse_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("选择游戏客户端"), self.pathEdit.text() or "", "StarRail.exe (StarRail.exe);;可执行文件 (*.exe)"
        )
        if path:
            self.pathEdit.setText(path)

    def _on_save(self, silent: bool = False) -> bool:
        account = self._current_account()
        if account is None:
            return False
        values = self._widgets_snapshot()

        name = values["name"] or am.default_account_name()

        # ===== 新增校验 =====
        if self._is_name_duplicated(name, exclude_id=account["id"]):
            self._toast(tr("保存失败"), tr("账号名称「{name}」已存在，请使用其他名称").format(name=name), success=False)


            # 【新增】强制把输入框恢复为原本的账号名称，防止界面和底层数据不一致
            self.nameEdit.setText(account.get("name") or "")
            # 同步重置快照，避免触发“未保存修改”的误报
            self._loaded = dict(self._snapshot(account))

            return False
        # ====================

        # 【修改这里】获取真实 username
        current_text = self.userEdit.text().strip()
        if "*" in current_text:
            # 如果框里是星号，说明用户没动过它，从内存里取真值
            username = getattr(self.userEdit, '_real_text', "")
        else:
            # 用户点进去改过了，直接取新值
            username = current_text

        password = self.pwdInput.text()
        try:
            am.update_account(
                account["id"],
                name=name,
                channel=values["channel"],
                use_global_path=values["use_global_path"],
                game_path=values["game_path"],
                auto_login=values["auto_login"],
                relogin=values["relogin"],
                after_finish=values["after_finish"],
            )
            am.save_credential(account["id"], username, password)
            am.save_live_config(account["id"])
        except Exception as e:
            self._toast(tr("保存失败"), str(e), success=False)
            return False
        self._loaded = dict(values)
        self._loaded["credential"] = (username, password)
        self._reload_from_store()
        if not silent:
            self._toast(tr("配置已保存"), tr("已写入账号「{name}」").format(name=name))
        return True

    def _prepare_start(self) -> bool:
        account = self._current_account()
        if account is None:
            self._toast(tr("无法启动"), tr("请先在「多账号」页添加账号"), success=False)
            return False
        if self._is_dirty() and not self._on_save(silent=True):
            return False
        try:
            am.switch_account(account["id"], cfg)
        except Exception as e:
            self._toast(tr("应用配置失败"), str(e), success=False)
            return False
        return True

    def _on_start_current(self):
        if self._prepare_start():
            account = self._current_account()
            # [MULTI_ACCOUNT] 必须带上账号 ID，否则子进程会当成「完整运行」直接跑 main
            if account:
                signalBus.startTaskSignal.emit(f"multi_account --account {account['id']}")
            else:
                signalBus.startTaskSignal.emit("multi_account")

    def _on_start_all(self):
        if self._prepare_start():
            signalBus.startTaskSignal.emit("multi_account")

    def _is_name_duplicated(self, name: str, exclude_id: str | None = None) -> bool:
        """检查账号名称是否与现有账号重复（排除自身）"""
        for acc in am.list_accounts():
            if exclude_id and acc["id"] == exclude_id:
                continue
            if acc.get("name") == name:
                return True
        return False


def multi_account_interface(tr, scrollWidget) -> SettingCardGroup:
    """供 setting_interface.addSubInterface 使用的入口。"""
    return MultiAccountGroup(scrollWidget)
