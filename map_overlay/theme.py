"""Application chrome. Map images and route colors are outside the UI theme."""
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


CANVAS_BACKGROUND = "#1a202c"
CHROME = "#242a39"
LIGHT = dict(window="#eae3d4", panel="#f3ede0", control="#f3ede0", hover="#e3d9c4",
             text="#303644", muted="#565960", disabled="#716d66", border="#c1b59e",
             heading="#735831", rail="#dcd2be", field="#f8f3e9", track="#b4a895",
             gold="#dbc394", gold_hover="#e5cfa4", gold_border="#a5854c", on_gold="#2b2824")
DARK = dict(window="#283143", panel="#30384c", control="#39445a", hover="#46536b",
            text="#f4efe5", muted="#c1c8d5", disabled="#a5adbb", border="#53617a",
            heading="#d8bc89", rail="#242a39", field="#252d3e", track="#647087",
            gold="#d7bd8d", gold_hover="#e2cda7", gold_border="#e2cb9e", on_gold="#2a2826")
THEMES = {"light": LIGHT, "dark": DARK}
ICONS = Path(__file__).with_name("icons").as_posix()


def apply_theme(app, theme):
    colors = THEMES[theme]
    palette = QPalette()
    roles = QPalette.ColorRole
    for role, color in {
        roles.Window: colors["window"], roles.WindowText: colors["text"],
        roles.Base: colors["field"], roles.AlternateBase: colors["panel"], roles.Text: colors["text"],
        roles.Button: colors["control"], roles.ButtonText: colors["text"],
        roles.BrightText: colors["text"], roles.PlaceholderText: colors["muted"],
        roles.Highlight: colors["gold"], roles.HighlightedText: colors["on_gold"],
        roles.Mid: colors["track"], roles.Midlight: colors["border"], roles.Light: colors["field"],
        roles.Dark: colors["border"], roles.Shadow: colors["rail"],
        roles.Link: colors["heading"], roles.LinkVisited: colors["heading"],
        roles.ToolTipBase: colors["field"], roles.ToolTipText: colors["text"],
    }.items():
        palette.setColor(role, QColor(color))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(theme))


def control_styles(theme, scope=""):
    colors = THEMES[theme]
    prefix = f"{scope} " if scope else ""

    def selectors(*names):
        return ", ".join(prefix + name for name in names)

    return f"""
    {selectors('QPushButton', 'QComboBox', 'QSpinBox', 'QLineEdit')} {{
        color:{colors['text']}; background:{colors['control']}; border:1px solid {colors['border']};
        border-radius:5px; min-height:20px; padding:5px 9px;
    }}
    {selectors('QPushButton:enabled:hover', 'QComboBox:enabled:hover')} {{ background:{colors['hover']}; }}
    {selectors('QPushButton:pressed')} {{ background:{colors['field']}; }}
    {selectors('QPushButton:disabled', 'QComboBox:disabled', 'QSpinBox:disabled')} {{
        color:{colors['disabled']}; background:{colors['window']};
    }}
    {selectors('QPushButton:focus', 'QComboBox:focus', 'QSpinBox:focus', 'QLineEdit:focus')} {{
        border-color:{colors['heading']};
    }}
    {selectors('QPushButton#primary:enabled', 'QPushButton#segment:enabled:checked')} {{
        color:{colors['on_gold']}; background:{colors['gold']}; border-color:{colors['gold_border']};
    }}
    {selectors('QPushButton#primary:enabled:hover', 'QPushButton#segment:enabled:checked:hover')} {{
        background:{colors['gold_hover']};
    }}
    {selectors('QPushButton#screenshotMode:enabled:checked')} {{
        color:{colors['text']}; background:{colors['hover']}; border-color:{colors['gold_border']};
    }}
    {selectors('QPushButton#primary:focus', 'QPushButton#segment:checked:focus',
               'QPushButton#screenshotMode:checked:focus')} {{ border-color:{colors['heading']}; }}
    {selectors('QComboBox')} {{ padding-right:27px; }}
    {selectors('QComboBox::drop-down')} {{
        subcontrol-origin:padding; subcontrol-position:top right; width:26px;
        border:0; background:transparent;
    }}
    {selectors('QComboBox::down-arrow')} {{
        image:url("{ICONS}/chevron-down-{theme}.svg"); width:12px; height:8px;
    }}
    {selectors('QComboBox QAbstractItemView')} {{
        color:{colors['text']}; background:{colors['field']}; border:1px solid {colors['border']};
        selection-color:{colors['on_gold']}; selection-background-color:{colors['gold']};
    }}
    {selectors('QSpinBox', 'QLineEdit')} {{ background:{colors['field']}; }}
    {selectors('QSpinBox')} {{ padding-right:23px; }}
    {selectors('QSpinBox::up-button', 'QSpinBox::down-button')} {{
        subcontrol-origin:padding; width:22px; height:14px; border:0; background:transparent;
    }}
    {selectors('QSpinBox::up-button')} {{ subcontrol-position:top right; }}
    {selectors('QSpinBox::down-button')} {{ subcontrol-position:bottom right; }}
    {selectors('QSpinBox::up-arrow')} {{ image:url("{ICONS}/chevron-up-{theme}.svg"); width:10px; height:7px; }}
    {selectors('QSpinBox::down-arrow')} {{ image:url("{ICONS}/chevron-down-{theme}.svg"); width:10px; height:7px; }}
    {selectors('QFrame#segmentedControl')} {{
        background:{colors['field']}; border:1px solid {colors['border']}; border-radius:6px;
    }}
    {selectors('QPushButton#segment')} {{ background:transparent; border-color:transparent; min-height:18px; padding:4px 8px; }}
    {selectors('QPushButton#segment:enabled:hover')} {{ background:{colors['hover']}; }}
    {selectors('QPushButton#segment:enabled:checked:hover')} {{ background:{colors['gold_hover']}; }}
    {selectors('QPushButton#segment:focus')} {{ border-color:{colors['gold_border']}; }}
    {selectors('QSlider::groove:horizontal')} {{ height:4px; border-radius:2px; background:{colors['track']}; }}
    {selectors('QSlider::sub-page:horizontal')} {{ border-radius:2px; background:{colors['gold_border']}; }}
    {selectors('QSlider::handle:horizontal')} {{
        width:13px; margin:-5px 0; border-radius:6px; background:{colors['gold']};
        border:1px solid {colors['gold_border']};
    }}
    {selectors('QSlider:focus::handle:horizontal')} {{ border-color:{colors['heading']}; }}
    """


def stylesheet(theme):
    p = THEMES[theme]
    return f"""
    QWidget {{ font-family:'Microsoft YaHei UI'; font-size:13px; }}
    QMainWindow, QDialog {{ background:{p['window']}; }}
    QLabel {{ color:{p['text']}; }}
    QLabel#muted, QLabel#shortcutHint {{ color:{p['muted']}; }}
    QLabel#shortcutHint:disabled {{ color:{p['disabled']}; }}
    QLabel#primaryShortcut {{ color:{p['on_gold']}; }}
    QPushButton#primary {{ font-weight:700; }}
    QPushButton#primary[hasShortcut="true"], QPushButton#screenshotMode {{ text-align:left; padding-left:11px; }}
    QLabel#shortcutHint, QLabel#primaryShortcut {{ font-family:'Segoe UI'; font-size:11px; }}
    QToolTip {{ color:{p['text']}; background:{p['field']}; border:1px solid {p['border']}; }}
    QGraphicsView {{ background:{CANVAS_BACKGROUND}; border:1px solid {p['border']}; border-radius:6px; }}
    QCheckBox {{ color:{p['text']}; spacing:5px; }}
    QSplitter::handle {{ background:{p['border']}; }}
    QScrollArea {{ border:0; }}
    QStatusBar {{ color:{DARK['muted']}; background:{CHROME}; }}
    QLabel#section, QLabel#dialogStep {{ color:{p['heading']}; font-weight:700; }}
    QLabel#dialogHeading {{ font-size:21px; font-weight:700; }}
    QLabel#panelHeading {{ font-size:14px; font-weight:650; }}
    QFrame#workflowPanel, QFrame#settingsCard {{
        background:{p['panel']}; border:1px solid {p['border']}; border-radius:7px;
    }}
    QFrame#brandBar {{ background:{CHROME}; border-bottom:1px solid {DARK['border']}; }}
    QLabel#brandName {{ color:{DARK['text']}; font-size:19px; font-weight:700; }}
    QLabel#brandDescription {{ color:{DARK['muted']}; }}
    QFrame#sidebar {{ background:{p['window']}; border-right:1px solid {p['border']}; }}
    QFrame#sidebarDivider {{ background:{p['border']}; }}
    QFrame#editor {{ background:{DARK['panel']}; }}
    #editor QLabel {{ color:{DARK['text']}; }}
    #editor QLabel#muted, #editor QLabel#overlayState {{ color:{DARK['muted']}; }}
    #editor QLabel#regionTitle {{ font-size:20px; font-weight:700; }}
    #editor QLabel#emptyMapTitle {{ font-size:19px; font-weight:650; }}
    #editor QFrame#emptyMap, #editor QLabel#minimapPreview, #editor QGraphicsView {{
        background:{CANVAS_BACKGROUND}; border:1px solid {DARK['border']}; border-radius:6px;
    }}
    #editor #previewTray {{ background:{DARK['window']}; border:1px solid {DARK['border']}; border-radius:6px; }}
    QFrame#settingsSidebar {{ background:{p['rail']}; border-right:1px solid {p['border']}; }}
    QListWidget#settingsCategories {{ background:transparent; color:{p['text']}; border:0; outline:0; }}
    QListWidget#settingsCategories::item {{ min-height:34px; padding:0 12px; border:1px solid transparent; border-radius:5px; }}
    QListWidget#settingsCategories::item:hover {{ background:{p['hover']}; }}
    QListWidget#settingsCategories::item:selected {{ color:{p['on_gold']}; background:{p['gold']}; }}
    QListWidget#settingsCategories::item:focus {{ border-color:{p['heading']}; }}
    QLabel#aboutAppName {{ font-size:21px; font-weight:700; }}
    QLabel#aboutLinkIcon {{ background:{LIGHT['field']}; border:1px solid {p['border']}; border-radius:6px; }}
    {control_styles(theme)}
    {control_styles('dark', '#editor')}
    #editor QPushButton#previewToggle, QPushButton#settingsButton {{ background:transparent; border-color:transparent; }}
    #editor QPushButton#previewToggle:hover {{ background:{DARK['hover']}; }}
    QPushButton#settingsButton:hover {{ background:{p['hover']}; }}
    #editor QPushButton#previewToggle:focus {{ border-color:{DARK['gold_border']}; }}
    QPushButton#settingsButton:focus {{ border-color:{p['heading']}; }}
    """


def segmented_buttons(labels, name, current=0):
    frame = QFrame()
    frame.setObjectName("segmentedControl")
    frame.setAccessibleName(name)
    row = QHBoxLayout(frame)
    row.setContentsMargins(3, 3, 3, 3)
    row.setSpacing(2)
    group = QButtonGroup(frame)
    buttons = []
    for index, label in enumerate(labels):
        button = QPushButton(label)
        button.setObjectName("segment")
        button.setAutoDefault(False)
        button.setCheckable(True)
        group.addButton(button)
        button.setChecked(index == current)
        row.addWidget(button)
        buttons.append(button)
    return frame, buttons


def workflow_layout(dialog, heading, *, step=None):
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 17, 20, 16)
    layout.setSpacing(10)
    if step:
        label = QLabel(step)
        label.setObjectName("dialogStep")
        layout.addWidget(label)
    title = QLabel(heading)
    title.setObjectName("dialogHeading")
    layout.addWidget(title)
    return layout, title
