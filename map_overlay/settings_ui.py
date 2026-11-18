"""Categorized program preferences; persistence stays in MainWindow."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel,
                              QListWidget, QListWidgetItem, QPushButton,
                              QScrollArea, QStackedWidget, QVBoxLayout, QWidget)


SETTINGS_STYLE = """
QDialog#settingsDialog { background:#0c131a; }
QDialog#settingsDialog QLabel { background:transparent; }
QWidget#settingsSidebar { background:#15212c; border-right:1px solid #263744; }
QLabel#settingsBrand { background:transparent; font-size:20px; font-weight:700; }
QLabel#settingsHint { background:transparent; color:#91a6b6; font-size:12px; }
QListWidget#settingsCategories { background:transparent; border:0; outline:0; padding:0; }
QListWidget#settingsCategories::item { height:42px; padding-left:15px; margin-bottom:6px; border-radius:7px; }
QListWidget#settingsCategories::item:hover { background:#223441; }
QListWidget#settingsCategories::item:selected { background:#28483f; color:#81f0cc; }
QListWidget#settingsCategories::item:focus { border:1px solid #65e7bd; }
QWidget#settingsContent, QWidget#settingsPage, QScrollArea#settingsScroll,
QWidget#settingsFooter { background:#0c131a; border:0; }
QLabel#settingsPageTitle { font-size:22px; font-weight:700; }
QLabel#settingsSection { color:#b9cbd6; font-weight:600; }
QFrame#settingsCard { background:#192733; border:1px solid #293d4b; border-radius:10px; }
QFrame#settingsCard QLabel, QFrame#settingsCard QWidget { background:transparent; }
QLabel#settingsItemTitle { font-size:14px; font-weight:600; }
QLabel#settingsDescription { color:#91a6b6; line-height:1.5; }
QPushButton#settingsClose { text-align:center; padding:7px 22px; }
QScrollBar:vertical { background:#0c131a; width:8px; }
QScrollBar::handle:vertical { background:#304553; border-radius:4px; min-height:30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
"""


class SettingsSwitch(QCheckBox):
    """A keyboard-accessible checkbox painted as a compact switch."""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setAccessibleName(label)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self):
        return QSize(50, 30)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        color = "#32987d" if self.isChecked() else "#526574"
        painter.setBrush(QColor(color if self.isEnabled() else "#2b3b46"))
        painter.drawRoundedRect(3, 4, 44, 22, 11, 11)
        painter.setBrush(QColor("#f1faf8"))
        painter.drawEllipse(27 if self.isChecked() else 6, 7, 16, 16)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor("#81f0cc"))
            painter.drawRoundedRect(0, 1, 49, 28, 14, 14)


class SettingsDialog(QDialog):
    def __init__(self, preview_enabled, parent=None, *, minimize_on_tracking=True):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("设置 · 循图")
        self.setStyleSheet(SETTINGS_STYLE)
        self.resize(900, 600)
        self.setMinimumSize(700, 480)
        self.category_indexes = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(180)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(14, 24, 14, 18)
        navigation.setSpacing(18)
        title = QLabel("设置")
        title.setObjectName("settingsBrand")
        navigation.addWidget(title)
        self.categories = QListWidget()
        self.categories.setObjectName("settingsCategories")
        self.categories.setAccessibleName("设置分类")
        navigation.addWidget(self.categories, 1)
        hint = QLabel("循图  /  ROUTE")
        hint.setObjectName("settingsHint")
        navigation.addWidget(hint)
        layout.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("settingsContent")
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        footer = QWidget()
        footer.setObjectName("settingsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(28, 14, 28, 18)
        saved_hint = QLabel("更改后立即生效并自动保存")
        saved_hint.setObjectName("settingsHint")
        footer_layout.addWidget(saved_hint, 1)
        close = QPushButton("关闭")
        close.setObjectName("settingsClose")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        footer_layout.addWidget(close)
        body.addWidget(footer)
        layout.addWidget(content, 1)

        general = self.add_category("general", "通用", "管理程序的日常使用偏好。")
        self.add_section(general, "窗口行为")
        self.minimize_toggle = self.add_switch(
            general, "开始叠加后自动最小化",
            "开启后最小化程序并切回游戏。\n"
            "关闭后保留当前窗口，手动切回游戏即可显示叠加层。", minimize_on_tracking)
        self.add_section(general, "实时预览")
        self.preview_toggle = self.add_switch(
            general, "实时同步小地图",
            "自动更新主界面的小地图预览，每秒最多 3 次。\n"
            "仅截取画面；关闭后保留最后画面，不影响路线叠加。", preview_enabled)
        general.addStretch()

        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.select_category("general")

    def add_category(self, key, title, description):
        """Register a navigation entry and its scrollable page together."""
        self.category_indexes[key] = self.pages.count()
        item = QListWidgetItem(title)
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.categories.addItem(item)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("settingsPage")
        column = QVBoxLayout(page)
        column.setContentsMargins(28, 26, 28, 20)
        column.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("settingsPageTitle")
        column.addWidget(heading)
        column.addWidget(self.description(description))
        scroll.setWidget(page)
        self.pages.addWidget(scroll)
        return column

    def select_category(self, key):
        self.categories.setCurrentRow(self.category_indexes[key])

    def add_switch(self, column, title, description, enabled):
        card = self.add_card(column)
        row = QHBoxLayout()
        text = QVBoxLayout()
        name = QLabel(title)
        name.setObjectName("settingsItemTitle")
        text.addWidget(name)
        text.addWidget(self.description(description))
        row.addLayout(text, 1)
        toggle = SettingsSwitch(title)
        toggle.setToolTip(description)
        toggle.setAccessibleDescription(description)
        toggle.setChecked(enabled)
        row.addWidget(toggle)
        card.addLayout(row)
        return toggle

    @staticmethod
    def description(text):
        label = QLabel(text)
        label.setObjectName("settingsDescription")
        label.setWordWrap(True)
        return label

    @staticmethod
    def add_section(column, title):
        label = QLabel(title)
        label.setObjectName("settingsSection")
        column.addWidget(label)

    @staticmethod
    def add_card(column):
        frame = QFrame()
        frame.setObjectName("settingsCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        column.addWidget(frame)
        return layout
