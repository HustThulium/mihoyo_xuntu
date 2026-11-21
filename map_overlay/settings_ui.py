"""Categorized program preferences; persistence stays in MainWindow."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QSize
from PySide6.QtGui import QIcon, QPainter, QPalette
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                              QListWidget, QListWidgetItem, QPushButton,
                              QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import APP_DESCRIPTION, APP_NAME, __version__
from .settings import DEFAULT_THEME, THEME_CHOICES
from .theme import ICONS


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
        palette = self.palette()
        roles = QPalette.ColorRole
        active = self.isChecked() and self.isEnabled()
        painter.setBrush(palette.color(roles.Highlight if active else roles.Mid))
        painter.drawRoundedRect(3, 4, 44, 22, 11, 11)
        painter.setBrush(palette.color(roles.HighlightedText if active else roles.Base))
        painter.drawEllipse(27 if self.isChecked() else 6, 7, 16, 16)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(palette.color(roles.Link))
            painter.drawRoundedRect(0, 1, 49, 28, 14, 14)


class AboutLink(QLabel):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.setOpenExternalLinks(True)
        self.setWordWrap(True)
        self.update_link()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and self.text():
            self.update_link()

    def update_link(self):
        color = self.palette().color(QPalette.ColorRole.Link).name()
        self.setText(f'<a href="{self.url}" style="color:{color}">{self.url}</a>')


class SettingsDialog(QDialog):
    def __init__(self, preview_enabled, parent=None, *, minimize_on_tracking=True, theme=DEFAULT_THEME):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle(f"设置 · {APP_NAME}")
        self.resize(780, 490)
        self.setMinimumSize(620, 390)
        self.category_indexes = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(153)
        navigation = QVBoxLayout(sidebar)
        navigation.setContentsMargins(14, 24, 14, 18)
        navigation.setSpacing(18)
        title = QLabel("设置")
        title.setObjectName("dialogHeading")
        navigation.addWidget(title)
        self.categories = QListWidget()
        self.categories.setObjectName("settingsCategories")
        self.categories.setAccessibleName("设置分类")
        navigation.addWidget(self.categories, 1)
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
        saved_hint.setObjectName("muted")
        footer_layout.addWidget(saved_hint)
        footer_layout.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("settingsClose")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        footer_layout.addWidget(close)
        body.addWidget(footer)
        layout.addWidget(content, 1)

        general = self.add_category("general", "通用")
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

        appearance = self.add_category("appearance", "外观")
        card = self.add_card(appearance)
        row = QHBoxLayout()
        name = QLabel("外观模式")
        name.setObjectName("panelHeading")
        row.addWidget(name, 1)
        self.theme_picker = QComboBox()
        self.theme_picker.setAccessibleName("外观模式")
        self.theme_picker.setMinimumWidth(106)
        for key, label in THEME_CHOICES:
            self.theme_picker.addItem(label, key)
        self.theme_picker.setCurrentIndex(self.theme_picker.findData(theme))
        row.addWidget(self.theme_picker)
        card.addLayout(row)
        appearance.addStretch()

        about = self.add_category("about", "关于")
        self.add_about_details(about)
        about.addStretch()

        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.pages.currentChanged.connect(
            lambda index: saved_hint.setVisible(index != self.category_indexes["about"]))
        self.select_category("general")

    def add_about_details(self, column):
        summary = self.add_card(column)
        row = QHBoxLayout()
        row.setSpacing(16)
        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(QSize(52, 52), self.devicePixelRatioF()))
        row.addWidget(icon)
        details = QVBoxLayout()
        details.setSpacing(4)
        name = QLabel(APP_NAME)
        name.setObjectName("aboutAppName")
        details.addWidget(name)
        details.addWidget(self.description(f"{APP_DESCRIPTION} · 版本 {__version__}"))
        row.addLayout(details, 1)
        summary.addLayout(row)

        links = self.add_card(column)
        self.add_about_link(links, "github", "项目 GitHub", "https://github.com/HustThulium/mihoyo_xuntu")
        self.add_about_link(links, "bilibili", "作者 B 站", "https://space.bilibili.com/603381")

    def add_about_link(self, column, icon_name, title, url):
        row = QHBoxLayout()
        row.setSpacing(12)
        icon = QLabel()
        icon.setObjectName("aboutLinkIcon")
        icon.setFixedSize(36, 36)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(QIcon(f"{ICONS}/{icon_name}.svg").pixmap(QSize(19, 19), self.devicePixelRatioF()))
        row.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)
        details = QVBoxLayout()
        details.setSpacing(4)
        name = QLabel(title)
        name.setObjectName("panelHeading")
        details.addWidget(name)
        details.addWidget(AboutLink(url))
        row.addLayout(details, 1)
        column.addLayout(row)

    def add_category(self, key, title):
        """Register a navigation entry and its scrollable page together."""
        self.category_indexes[key] = self.pages.count()
        item = QListWidgetItem(title)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setSizeHint(QSize(0, 38))
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
        heading.setObjectName("dialogHeading")
        column.addWidget(heading)
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
        name.setObjectName("panelHeading")
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
        label.setObjectName("muted")
        label.setWordWrap(True)
        return label

    @staticmethod
    def add_section(column, title):
        label = QLabel(title)
        label.setObjectName("section")
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
