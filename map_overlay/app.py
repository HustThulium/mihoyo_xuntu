from __future__ import annotations

from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer, QStandardPaths, QRectF, QSize, QSignalBlocker
from PySide6.QtGui import QPainter, QPixmap, QIcon, QKeySequence, QShortcut, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                              QVBoxLayout, QPushButton, QLabel, QFileDialog,
                              QMessageBox, QComboBox, QFrame, QStackedWidget,
                              QInputDialog, QColorDialog, QSlider,
                              QScrollArea)

from . import APP_DESCRIPTION, APP_NAME, native
from .annotations import MAX_ERASER_DIAMETER, affine_transform, paint_minimap
from .capture import CapturePause, TrackingWorker, PreviewWorker, grab
from .geometry import inverse
from .project import Project, read_image
from .route_import import RouteExtraction
from .route_import_ui import RouteAreaDialog, RouteMatchDialog, RoutePreviewDialog
from .settings import MinimapPosition, MinimapSettings, portable_settings_path
from .settings_ui import SettingsDialog
from .theme import apply_theme, segmented_buttons
from .vision import Localizer
from .widgets import (AnnotationView, RegionDialog, CalibrationDialog,
                      RouteOverlay, MinimapSettingsDialog, ink_size_control, qimage)


APP_TITLE = f"{APP_NAME} · {APP_DESCRIPTION}"
APP_ICON_PATH = Path(__file__).with_name("icons") / "xuntu.ico"


class MainWindow(QMainWindow):
    def __init__(self, settings_path=None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        QApplication.instance().setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(1240, 830)
        self.project = None
        self.project_path = None
        self.dirty = False
        self.busy = False
        self.localizer = None
        self.worker = None
        self.preview_worker = None
        self.preview_generation = 0
        self.live_preview_image = None
        self.seed = None
        self.generation = 0
        self.last_visible = 0.0
        self.capture_pause = CapturePause()
        self.screenshot_pending = False
        self.last_preview = None
        self.last_preview_matrix = None
        self.minimap_preview = None
        self.preview_at = 0
        legacy_path = None
        if settings_path is None:
            settings_path = portable_settings_path()
            app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
            if app_data:
                legacy_path = Path(app_data) / "settings.json"
        self.minimap_settings = MinimapSettings(settings_path, legacy_path=legacy_path)
        apply_theme(QApplication.instance(), self.minimap_settings.theme)
        self.overlay = RouteOverlay(self.minimap_settings.ink_opacity / 100)
        self.build_ui()
        self.refresh_windows()
        self.registered = native.register_hotkeys(int(self.winId()))
        if len(self.registered) != len(native.HOTKEYS):
            self.statusBar().showMessage("部分全局快捷键注册失败，请使用主界面按钮。", 6500)
        if native.SCREENSHOT_HOTKEY not in self.registered:
            self.screenshot_button.setToolTip("Ctrl+Alt+F11 注册失败，可用此按钮切换截图模式。")
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self.expire_overlay)
        self.watchdog.start(60)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_project)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.canvas.undo)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("brandBar")
        brand = QHBoxLayout(bar)
        brand.setContentsMargins(18, 8, 18, 8)
        mark = QLabel()
        mark.setObjectName("brandMark")
        mark.setPixmap(self.windowIcon().pixmap(QSize(24, 24), self.devicePixelRatioF()))
        brand.addWidget(mark)
        name = QLabel(APP_NAME)
        name.setObjectName("brandName")
        brand.addWidget(name)
        brand.addSpacing(6)
        description = QLabel(APP_DESCRIPTION)
        description.setObjectName("brandDescription")
        brand.addWidget(description)
        brand.addStretch()
        outer.addWidget(bar)
        workspace = QHBoxLayout()
        workspace.setSpacing(0)
        workspace.addWidget(self.build_sidebar())
        workspace.addWidget(self.build_editor(), 1)
        outer.addLayout(workspace, 1)

        notifications = self.statusBar()
        notifications.setSizeGripEnabled(False)
        notifications.hide()
        notifications.messageChanged.connect(lambda message: notifications.setVisible(bool(message)))
        self.settings_dialog = SettingsDialog(
            self.minimap_settings.preview_enabled, parent=self,
            minimize_on_tracking=self.minimap_settings.minimize_on_tracking,
            theme=self.minimap_settings.theme)
        self.preview_toggle = self.settings_dialog.preview_toggle
        self.preview_toggle.toggled.connect(self.toggle_preview)
        self.settings_dialog.minimize_toggle.toggled.connect(self.toggle_minimize_on_tracking)
        self.settings_dialog.theme_picker.currentIndexChanged.connect(self.change_theme)

    def build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(250)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 10, 12, 9)
        side.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel = QWidget()
        controls = QVBoxLayout(panel)
        controls.setContentsMargins(4, 6, 4, 6)
        controls.setSpacing(7)

        def section(text, divider=True):
            if divider:
                line = QFrame()
                line.setObjectName("sidebarDivider")
                line.setFixedHeight(1)
                controls.addSpacing(5)
                controls.addWidget(line)
                controls.addSpacing(3)
            label = QLabel(text)
            label.setObjectName("section")
            controls.addWidget(label)

        section("游戏窗口", divider=False)
        row = QHBoxLayout()
        self.targets = QComboBox()
        self.targets.setMinimumWidth(0)
        self.targets.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.targets.setMinimumContentsLength(8)
        self.targets.currentIndexChanged.connect(self.target_changed)
        row.addWidget(self.targets, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_windows)
        row.addWidget(refresh)
        controls.addLayout(row)
        self.minimap_position_button = QPushButton("小地图位置设置…")
        self.minimap_position_button.clicked.connect(self.open_minimap_settings)
        controls.addWidget(self.minimap_position_button)
        self.minimap_status = QLabel()
        self.minimap_status.setObjectName("muted")
        self.minimap_status.setWordWrap(True)
        controls.addWidget(self.minimap_status)
        section("区域与路线")
        row = QHBoxLayout()
        new = QPushButton("新建区域")
        new.setToolTip("从游戏大地图建立区域")
        new.clicked.connect(self.new_capture)
        row.addWidget(new, 1)
        open_region = QPushButton("打开区域")
        open_region.clicked.connect(self.open_project)
        row.addWidget(open_region, 1)
        controls.addLayout(row)
        self.import_route_button = QPushButton("导入路线图…")
        self.import_route_button.clicked.connect(self.import_route)
        controls.addWidget(self.import_route_button)
        self.save_button = QPushButton("保存区域")
        self.save_button.setObjectName("primary")
        self.save_button.setToolTip("保存区域（Ctrl+S）")
        self.save_button.clicked.connect(self.save_project)
        controls.addWidget(self.save_button)

        section("游戏内叠加")
        self.track_button = self.shortcut_button("开始叠加", "Ctrl+Alt+F9", "primary", self.toggle_tracking)
        self.track_button.setMinimumHeight(37)
        controls.addWidget(self.track_button)
        self.screenshot_button = self.shortcut_button("截图模式", "Ctrl+Alt+F11", "screenshotMode",
                                                     self.toggle_screenshot_mode)
        self.screenshot_button.setCheckable(True)
        self.screenshot_button.setEnabled(False)
        self.screenshot_button.setToolTip("暂停定位并允许截图。再次点击或按快捷键恢复定位。")
        controls.addWidget(self.screenshot_button)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("墨迹不透明度"), 1)
        self.ink_slider = QSlider(Qt.Orientation.Horizontal)
        self.ink_slider.setAccessibleName("小地图墨迹不透明度")
        self.ink_slider.setToolTip("向右拖动让墨迹更清晰，100% 为完全不透明")
        self.ink_slider.setRange(0, 100)
        self.ink_slider.setValue(self.minimap_settings.ink_opacity)
        self.ink_slider.setTracking(False)
        self.ink_percentage = QLabel(f"{self.ink_slider.value()}%")
        self.ink_percentage.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ink_slider.sliderMoved.connect(self.show_ink_opacity)
        self.ink_slider.valueChanged.connect(self.save_ink_opacity)
        opacity_row.addWidget(self.ink_percentage)
        controls.addLayout(opacity_row)
        controls.addWidget(self.ink_slider)
        controls.addStretch(1)
        scroll.setWidget(panel)
        side.addWidget(scroll, 1)
        line = QFrame()
        line.setObjectName("sidebarDivider")
        line.setFixedHeight(1)
        side.addWidget(line)
        self.settings_button = QPushButton("设置…")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setAccessibleName("设置")
        self.settings_button.clicked.connect(self.open_settings)
        side.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignLeft)
        return sidebar

    @staticmethod
    def shortcut_button(text, shortcut, name, callback):
        button = QPushButton(text)
        button.setObjectName(name)
        button.setProperty("hasShortcut", True)
        button.setAccessibleDescription(shortcut)
        button.clicked.connect(callback)
        layout = QHBoxLayout(button)
        layout.setContentsMargins(11, 0, 11, 0)
        layout.addStretch()
        hint = QLabel(shortcut)
        hint.setObjectName("primaryShortcut" if name == "primary" else "shortcutHint")
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(hint)
        return button

    def build_editor(self):
        editor = QFrame()
        editor.setObjectName("editor")
        content = QVBoxLayout(editor)
        content.setContentsMargins(14, 10, 14, 8)
        content.setSpacing(8)
        region = QHBoxLayout()
        self.area_label = QLabel("路线编辑")
        self.area_label.setObjectName("regionTitle")
        self.area_label.setMinimumWidth(1)
        region.addWidget(self.area_label)
        self.area_status = QLabel()
        self.area_status.setObjectName("muted")
        self.area_status.hide()
        region.addWidget(self.area_status)
        region.addStretch(1)
        self.canvas = AnnotationView()
        self.undo_button = QPushButton("撤销")
        self.undo_button.setToolTip("撤销（Ctrl+Z）")
        self.undo_button.clicked.connect(self.canvas.undo)
        region.addWidget(self.undo_button)
        content.addLayout(region)
        self.build_annotation_tools(content)
        self.canvas.changed.connect(self.annotations_changed)
        self.map_stack = QStackedWidget()
        empty = QFrame()
        empty.setObjectName("emptyMap")
        placeholder = QVBoxLayout(empty)
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title = QLabel("还没有区域", alignment=Qt.AlignmentFlag.AlignCenter)
        empty_title.setObjectName("emptyMapTitle")
        placeholder.addWidget(empty_title)
        empty_hint = QLabel("从游戏大地图新建区域，或打开已有区域文件。", alignment=Qt.AlignmentFlag.AlignCenter)
        empty_hint.setObjectName("muted")
        empty_hint.setWordWrap(True)
        placeholder.addWidget(empty_hint)
        self.map_stack.addWidget(empty)
        self.map_stack.addWidget(self.canvas)
        content.addWidget(self.map_stack, 1)
        footer = QHBoxLayout()
        self.state = QLabel("等待准备地图")
        self.state.setObjectName("overlayState")
        self.state.setWordWrap(True)
        footer.addWidget(self.state, 1)
        self.preview_button = QPushButton("小地图预览  +")
        self.preview_button.setObjectName("previewToggle")
        self.preview_button.setAccessibleName("小地图预览")
        self.preview_button.setCheckable(True)
        self.preview_button.toggled.connect(self.set_preview_expanded)
        footer.addWidget(self.preview_button)
        content.addLayout(footer)
        content.addWidget(self.build_preview())
        self.refresh_annotation_controls()
        return editor

    def build_preview(self):
        self.preview_tray = QFrame()
        self.preview_tray.setObjectName("previewTray")
        self.preview_tray.hide()
        row = QHBoxLayout(self.preview_tray)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(14)
        self.preview = QLabel("小地图识别预览")
        self.preview.setObjectName("minimapPreview")
        self.preview.setFixedSize(210, 210)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        heading = QLabel("小地图预览")
        heading.setObjectName("panelHeading")
        info.addWidget(heading)
        self.preview_caption = QLabel("等待画面")
        self.preview_caption.setObjectName("muted")
        self.preview_caption.setWordWrap(True)
        info.addWidget(self.preview_caption)
        details_button = QPushButton("定位详情  +")
        details_button.setCheckable(True)
        info.addWidget(details_button, alignment=Qt.AlignmentFlag.AlignLeft)
        diagnostics = QWidget()
        details = QVBoxLayout(diagnostics)
        details.setContentsMargins(0, 0, 0, 0)
        self.metrics = QLabel("匹配点 —    图像拟合误差 —    处理时间 —")
        self.metrics.setWordWrap(True)
        self.metrics.setObjectName("muted")
        details.addWidget(self.metrics)
        info.addWidget(diagnostics)
        diagnostics.hide()
        details_button.toggled.connect(diagnostics.setVisible)
        details_button.toggled.connect(lambda expanded: details_button.setText("定位详情  −" if expanded else "定位详情  +"))
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        info.addWidget(self.detail)
        info.addStretch()
        row.addLayout(info, 1)
        return self.preview_tray

    def set_preview_expanded(self, expanded):
        self.preview_tray.setVisible(expanded)
        self.preview_button.setText("收起小地图预览  −" if expanded else "小地图预览  +")
        self.preview_button.setAccessibleName("收起小地图预览" if expanded else "小地图预览")

    def build_annotation_tools(self, content):
        self.annotation_toolbar = QWidget()
        tools = QHBoxLayout(self.annotation_toolbar)
        tools.setContentsMargins(0, 0, 0, 0)
        modes = ("draw", "line", "erase", "view")
        mode_frame, mode_buttons = segmented_buttons(("画笔", "直线", "橡皮擦", "浏览"), "编辑工具",
                                                      current=modes.index(self.canvas.mode))
        tools.addWidget(mode_frame)
        for button, mode in zip(mode_buttons, modes):
            button.toggled.connect(lambda checked, mode=mode: checked and self.change_canvas_mode(mode))
        self.brush_button = QPushButton("颜色…")
        self.brush_button.setAccessibleName("画笔颜色")
        self.brush_button.clicked.connect(self.choose_color)
        tools.addWidget(self.brush_button)
        self.line_width = ink_size_control("线宽", 24, self.canvas.ink_width)
        self.line_width.valueChanged.connect(self.canvas.set_ink_width)
        tools.addWidget(self.line_width)
        self.eraser_size = ink_size_control("直径", MAX_ERASER_DIAMETER, self.canvas.eraser_diameter)
        self.eraser_size.valueChanged.connect(self.canvas.set_eraser_diameter)
        tools.addWidget(self.eraser_size)
        tools.addStretch(1)

        display, (_, ink_only) = segmented_buttons(("叠加底图", "只看墨迹"), "显示内容")
        ink_only.toggled.connect(self.canvas.set_ink_only)
        tools.addWidget(display)
        fit = QPushButton("适应窗口")
        fit.clicked.connect(self.canvas.fit_image)
        tools.addWidget(fit)
        content.addWidget(self.annotation_toolbar)

    def error(self, exception):
        QMessageBox.warning(self, "无法完成操作", str(exception))

    def choose_file(self, title, file_filter, *, filename=None):
        if filename is None:
            selected, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        else:
            selected, _ = QFileDialog.getSaveFileName(self, title, filename, file_filter)
        return selected

    def hwnd(self):
        handle = self.targets.currentData()
        if not handle:
            raise ValueError("请先选择游戏窗口并刷新列表。")
        return handle

    def refresh_windows(self):
        previous = self.targets.currentData()
        self.targets.blockSignals(True)
        self.targets.clear()
        self.targets.addItem("请选择游戏窗口", None)
        for hwnd, title in native.windows():
            self.targets.addItem(title, hwnd)
        index = self.targets.findData(previous)
        if previous and index >= 0:
            self.targets.setCurrentIndex(index)
        else:
            for item in range(1, self.targets.count()):
                if any(s in self.targets.itemText(item).lower() for s in ("原神", "genshin impact")):
                    self.targets.setCurrentIndex(item)
                    break
        self.targets.blockSignals(False)
        self.target_changed()

    def target_changed(self):
        if self.worker is not None:
            self.stop_tracking(show=False)
        self.seed = None
        self.update_minimap_status()
        self.live_preview_image = None
        self.preview_caption.setText("实时同步等待新窗口画面")
        self.sync_preview()

    def current_client_size(self):
        target = self.targets.currentData()
        box = native.client_box(target, strict=False) if target else None
        return box[2:] if box else None

    def update_minimap_status(self):
        client_size = self.current_client_size()
        settings = self.minimap_settings
        status = settings.status(client_size)
        self.minimap_status.setToolTip(status)
        if settings.position and not settings.load_error and (client_size is None or settings.position.matches(client_size)):
            status = "小地图位置已设置"
        self.minimap_status.setText(status)

    def show_ink_opacity(self, value):
        self.ink_percentage.setText(f"{value}%")
        self.overlay.ink_opacity = value / 100
        self.overlay.update()
        if self.last_preview_matrix is not None:
            self.update_preview(self.last_preview, self.last_preview_matrix)

    def save_ink_opacity(self, value):
        try:
            self.minimap_settings.save_ink_opacity(value)
        except (OSError, ValueError) as exc:
            value = self.minimap_settings.ink_opacity
            with QSignalBlocker(self.ink_slider):
                self.ink_slider.setValue(value)
            self.show_ink_opacity(value)
            self.error(exc)
            return
        self.show_ink_opacity(value)

    def toggle_preview(self, enabled):
        try:
            self.minimap_settings.save_preview_enabled(enabled)
        except Exception as exc:
            self.preview_toggle.blockSignals(True)
            self.preview_toggle.setChecked(self.minimap_settings.preview_enabled)
            self.preview_toggle.blockSignals(False)
            self.error(exc)
            return
        self.sync_preview()

    def stop_preview(self):
        if self.preview_worker is None:
            return True
        self.preview_generation += 1
        self.preview_worker.stop()
        if not self.preview_worker.wait(5000):
            self.preview_caption.setText("实时同步正在停止，请稍后重试")
            return False
        self.preview_worker.deleteLater()
        self.preview_worker = None
        return True

    def toggle_minimize_on_tracking(self, enabled):
        try:
            self.minimap_settings.save_minimize_on_tracking(enabled)
        except Exception as exc:
            toggle = self.settings_dialog.minimize_toggle
            toggle.blockSignals(True)
            toggle.setChecked(self.minimap_settings.minimize_on_tracking)
            toggle.blockSignals(False)
            self.error(exc)

    def change_theme(self, index):
        picker = self.settings_dialog.theme_picker
        theme = picker.itemData(index)
        previous = self.minimap_settings.theme
        if theme == previous:
            return
        try:
            self.minimap_settings.save_theme(theme)
        except (OSError, ValueError) as exc:
            with QSignalBlocker(picker):
                picker.setCurrentIndex(picker.findData(previous))
            self.error(exc)
            return
        apply_theme(QApplication.instance(), theme)

    def restore_window(self):
        # showNormal() also clears WindowMaximized. Remove only minimization so
        # both maximized windows and normal windows retain their existing state.
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.activateWindow()

    def sync_preview(self):
        target = self.targets.currentData()
        position = self.minimap_settings.position
        active = bool(self.minimap_settings.preview_enabled and target and position and not self.busy)
        if (active and self.preview_worker is not None and
                self.preview_worker.hwnd == target and self.preview_worker.position == position):
            return
        if not self.stop_preview():
            return
        if not active:
            if not self.minimap_settings.preview_enabled:
                self.preview_caption.setText("实时同步已关闭 · 保留最后画面")
            elif not target:
                self.preview_caption.setText("实时同步等待选择游戏窗口")
            elif not position:
                self.preview_caption.setText("实时同步等待设置小地图位置")
            else:
                self.preview_caption.setText("实时同步已暂停：正在设置或绘制")
            return
        self.preview_generation += 1
        self.preview_worker = PreviewWorker(target, position, self.preview_generation, self.capture_pause)
        self.preview_worker.result.connect(self.receive_preview)
        self.preview_worker.start()

    def receive_preview(self, payload):
        if payload["generation"] != self.preview_generation or self.preview_worker is None:
            return
        if payload.get("fatal"):
            self.preview_caption.setText(payload["status"])
            self.stop_preview()
            return
        if self.capture_pause.paused or payload.get("capture_revision", 0) != self.capture_pause.revision:
            return
        if payload["image"] is None:
            self.preview_caption.setText(payload["status"])
        else:
            target = self.preview_worker.hwnd
            current = native.client_box(target, strict=False)
            position = self.preview_worker.position
            valid = bool(time.monotonic() - payload["time"] <= .5 and current and
                         position.matches(current[2:]) and native.foreground_is(target) and
                         position.absolute_box(current) == payload["box"])
            if valid:
                self.live_preview_image = payload["image"]
                self.update_preview(self.live_preview_image, None, payload["status"])
            else:
                self.preview_caption.setText("实时同步已暂停：画面过期或窗口状态变化")

    def confirm_discard(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "保存修改", "当前区域有未保存修改，是否先保存？",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def set_project(self, project, path=None):
        self.stop_tracking(show=False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.localizer = Localizer(project.image)
            self.project, self.project_path = project, path
            self.seed = None
            self.canvas.set_project(project)
        finally:
            QApplication.restoreOverrideCursor()
        self.dirty = path is None
        self.map_stack.setCurrentWidget(self.canvas)
        self.refresh_annotation_controls()
        self.update_label()
        self.state.setText("底图已准备 · 可以绘制或导入路线")
        self.metrics.setText(f"底图 {project.image.shape[1]} × {project.image.shape[0]} · 特征点 {len(self.localizer.points)}")
        self.detail.clear()
        if self.live_preview_image is not None:
            self.update_preview(self.live_preview_image, None)
        elif self.minimap_preview is not None:
            self.update_preview(self.minimap_preview, None, "已设置的小地图范围 · 框选截图")
        else:
            self.preview.clear()
            self.preview.setText("小地图识别预览")
            self.preview_caption.setText("小地图预览")
            self.last_preview = None
            self.last_preview_matrix = None
        self.update_minimap_status()

    def update_label(self):
        if self.project:
            self.area_label.setText(self.project.name)
            self.area_label.setToolTip(self.project.name)
            status = "未保存" if self.dirty else "已保存"
            if self.project.markers:
                status += f" · {len(self.project.markers)} 个标记"
            self.area_status.setText(status)
            self.area_status.show()
            self.undo_button.setEnabled(bool(self.canvas.edits and self.canvas.edits.history))

    def annotations_changed(self):
        if self.project:
            self.dirty = True
            self.update_label()
            if self.last_preview is not None:
                self.update_preview(self.last_preview, self.last_preview_matrix)

    def change_canvas_mode(self, mode):
        self.canvas.set_mode(mode)
        self.refresh_annotation_controls()

    def refresh_annotation_controls(self):
        active = self.project is not None
        self.annotation_toolbar.setEnabled(active)
        drawing = self.canvas.mode in ("draw", "line")
        self.brush_button.setVisible(drawing)
        self.line_width.setVisible(drawing)
        self.eraser_size.setVisible(self.canvas.mode == "erase")
        self.import_route_button.setEnabled(active)
        self.save_button.setEnabled(active)
        self.undo_button.setEnabled(bool(self.canvas.edits and self.canvas.edits.history))
        self.refresh_brush_color()

    def refresh_brush_color(self):
        if self.project is not None:
            swatch = QPixmap(13, 13)
            swatch.fill(QColor(self.project.color))
            self.brush_button.setIcon(QIcon(swatch))

    def import_route(self):
        if self.busy or self.project is None:
            return
        self.busy = True
        area = None
        try:
            path = self.choose_file("导入路线图", "路线图 (*.png *.jpg *.jpeg *.bmp)")
            if not path:
                return
            source = read_image(path)
            area = RouteAreaDialog(source, self)
            while area.exec() == QDialogAccepted:
                selected = source
                if area.rect:
                    x, y, w, h = area.rect
                    selected = source[y:y+h, x:x+w]
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    match = self.localizer.register_route(selected)
                except ValueError:
                    match = None
                finally:
                    QApplication.restoreOverrideCursor()
                confirmation = RouteMatchDialog(selected, self.project.image, match, area)
                accepted = confirmation.exec() == QDialogAccepted
                reselect = confirmation.reselect
                confirmation.deleteLater()
                if reselect:
                    continue
                if not accepted or match is None:
                    break
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    extraction = RouteExtraction(self.project.image, selected, match.matrix, match.route_clip)
                    preview = RoutePreviewDialog(extraction, self.project, area)
                finally:
                    QApplication.restoreOverrideCursor()
                if preview.exec() == QDialogAccepted:
                    self.canvas.edits.merge_ink(extraction.origin, preview.view.ink)
                    self.canvas.viewport().update()
                    self.canvas.changed.emit()
                    self.statusBar().showMessage("已合入墨迹，可撤销本次导入。点击「保存区域」写入文件。", 4500)
                if not preview.reselect:
                    break
                preview.deleteLater()
        except Exception as exc:
            self.error(exc)
        finally:
            self.busy = False
            if area is not None:
                area.deleteLater()

    def require_project(self):
        if self.project is None:
            raise ValueError("请先从游戏大地图建立区域，或打开已有区域文件。")

    def capture_action(self, callback):
        if self.busy:
            return
        try:
            target = self.hwnd()
            box = native.client_box(target)
            native.monitor_for(box)
            if not self.stop_tracking(show=False):
                return
            self.busy = True
            if not self.stop_preview():
                self.busy = False
                return
            self.hide()
            native.focus(target)
        except Exception as exc:
            self.busy = False
            self.sync_preview()
            self.error(exc)
            return

        def capture():
            try:
                if not native.foreground_is(target):
                    raise ValueError("目标窗口未成为前台，未进行截图。请切回游戏后使用快捷键重试。")
                box = native.client_box(target)
                native.monitor_for(box)
                image = grab(box)
                if native.client_box(target) != box or not native.foreground_is(target):
                    raise ValueError("截图期间窗口发生变化，请重试。")
                callback(image, box)
            except Exception as exc:
                self.restore_window()
                self.error(exc)
            finally:
                self.busy = False
                self.restore_window()
                self.sync_preview()

        QTimer.singleShot(500, capture)

    def new_capture(self):
        if not self.confirm_discard():
            return

        def received(image, _box):
            selector = RegionDialog(image)
            if selector.exec() != QDialogAccepted:
                return
            name, ok = QInputDialog.getText(None, "区域名称", "输入区域名称", text="我的收集路线")
            if not ok:
                return
            x, y, w, h = selector.rect
            selected = image[y:y+h, x:x+w].copy()
            project = Project(selected, name.strip() or "未命名区域")
            project.target_title = self.targets.currentText()
            self.set_project(project)

        self.capture_action(received)

    def open_project(self, path=None):
        if not self.confirm_discard():
            return
        if not path or isinstance(path, bool):
            path = self.choose_file("打开区域", "循图区域 (*.routepack)")
        if path:
            try:
                project = Project.load(path)
                self.set_project(project, path)
                match = self.targets.findText(project.target_title)
                if match >= 0:
                    self.targets.setCurrentIndex(match)
            except Exception as exc:
                self.error(exc)

    def save_project(self):
        try:
            self.require_project()
            path = self.project_path
            if not path:
                path = self.choose_file("保存区域与路线", "循图区域 (*.routepack)",
                                        filename=self.project.name + ".routepack")
            if not path:
                return False
            if not str(path).lower().endswith(".routepack"):
                path += ".routepack"
            self.project.save(path)
            self.project_path = path
            self.dirty = False
            self.update_label()
            self.statusBar().showMessage(f"已保存：{path}", 4000)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def open_settings(self, checked=False):
        if self.busy or self.settings_dialog.isVisible():
            return
        self.settings_dialog.exec()

    def open_minimap_settings(self):
        if self.busy or self.settings_dialog.isVisible():
            return
        self.update_minimap_status()
        dialog = MinimapSettingsDialog(self.minimap_settings.status(self.current_client_size()),
                                       self.minimap_settings.position, parent=self)
        self.busy = True
        if not self.stop_preview():
            self.busy = False
            dialog.deleteLater()
            return
        try:
            accepted = dialog.exec() == QDialogAccepted
        finally:
            self.busy = False
            self.sync_preview()
            dialog.deleteLater()
        if accepted and dialog.action == "select":
            self.select_minimap()

    def save_minimap_position(self, position, preview_image):
        if not self.stop_tracking(show=False):
            return False
        first_selection = self.minimap_settings.position is None
        self.minimap_settings.save_position(position)
        self.seed = None
        if self.localizer:
            self.localizer.reset()
        self.minimap_preview = preview_image.copy()
        self.live_preview_image = None
        self.update_preview(self.minimap_preview, None, "位置设置成功 · 框选截图")
        if first_selection:
            self.preview_button.setChecked(True)
        self.update_minimap_status()
        self.state.setText("小地图位置设置成功 · 已保存")
        w, h = position.rect[2:]
        self.metrics.setText(f"小地图范围 {w} × {h} · 所有区域共用")
        self.detail.setText("请核对本次框选的小地图截图。需要调整时，可在“小地图位置设置”中重新框选。")
        self.statusBar().showMessage("小地图位置已自动保存，重启或切换区域后可继续使用。", 4500)
        self.sync_preview()
        return True

    def select_minimap(self):
        def received(image, box):
            selector = RegionDialog(image, mini=True)
            if selector.exec() == QDialogAccepted:
                # Keep this flow independent of map creation and terrain matching.
                position = MinimapPosition(selector.rect, box[2:])
                current = native.client_box(self.hwnd())
                if not position.matches(current[2:]):
                    raise ValueError("框选期间游戏窗口尺寸已变化，请重新框选。")
                x, y, w, h = selector.rect
                self.save_minimap_position(position, image[y:y+h, x:x+w])

        self.capture_action(received)

    def mini_from_capture(self, image, box):
        position = self.minimap_settings.require_position(box[2:])
        x, y, w, h = position.rect
        return image[y:y+h, x:x+w].copy()

    def calibrate_minimap(self):
        try:
            self.require_project()
        except ValueError as exc:
            self.error(exc)
            return

        def received(image, box):
            mini = self.mini_from_capture(image, box)
            dialog = CalibrationDialog(self.project.image, mini)
            if dialog.exec() == QDialogAccepted:
                self.seed = dialog.matrix
                self.localizer.reset(self.seed)
                match = self.localizer.locate(mini)
                self.show_match(mini, match)
                self.detail.clear()
                if not match.ok:
                    self.state.setText("地标校准已接受，但当前视觉匹配仍不足")
                    self.detail.setText("校准只提供搜索起点；没有当前画面的匹配证据时不会显示静态路线。\n请尝试细节更清楚的底图，或换到附近有明显道路的位置。")

        self.capture_action(received)

    def toggle_tracking(self):
        if self.busy:
            return
        if self.worker:
            self.stop_tracking()
            return
        try:
            self.require_project()
            target = self.hwnd()
            box = native.client_box(target)
            native.monitor_for(box)
            position = self.minimap_settings.require_position(box[2:])
            if not self.project.has_annotations:
                raise ValueError("请先绘制或导入路线墨迹，再开始叠加。")
            self.generation += 1
            self.worker = TrackingWorker(self.project.image, target, position, self.generation, self.seed,
                                         self.capture_pause)
            self.worker.result.connect(self.receive_frame)
            self.worker.start()
            self.track_button.setText("停止叠加")
            self.screenshot_button.setEnabled(True)
            self.state.setText("正在建立实时定位…")
            if self.minimap_settings.minimize_on_tracking:
                self.showMinimized()
                native.focus(target)
        except Exception as exc:
            self.error(exc)

    def stop_tracking(self, show=True):
        self.end_screenshot_mode()
        self.screenshot_button.setEnabled(False)
        self.overlay.hide()
        self.overlay.project = None
        self.last_visible = 0
        if self.worker:
            self.generation += 1
            self.worker.stop()
            if not self.worker.wait(5000):
                self.state.setText("定位线程仍在结束，请稍后重试")
                return False
            self.worker.deleteLater()
            self.worker = None
            self.state.setText("叠加已停止")
        self.track_button.setText("开始叠加")
        if show:
            self.restore_window()
        return True

    def toggle_screenshot_mode(self):
        if self.screenshot_pending or self.capture_pause.paused:
            self.end_screenshot_mode()
            return
        if self.busy or self.worker is None or self.settings_dialog.isVisible():
            self.screenshot_button.setChecked(False)
            self.statusBar().showMessage("请先开始叠加，显示路线后即可开启截图模式。", 4000)
            return
        self.screenshot_button.setChecked(True)
        self.expire_overlay()
        if self.overlay.isVisible():
            self.begin_screenshot_mode()
            return
        self.screenshot_pending = True
        self.screenshot_button.setText("等待定位…")
        if not native.foreground_is(self.worker.hwnd):
            if self.minimap_settings.minimize_on_tracking:
                self.showMinimized()
            native.focus(self.worker.hwnd)

    def begin_screenshot_mode(self):
        self.capture_pause.set_paused(True)
        if (time.monotonic() - self.last_visible > .35 or not self.overlay_box_matches(self.overlay.capture_box)
                or not native.foreground_is(self.worker.hwnd)):
            self.end_screenshot_mode()
            self.statusBar().showMessage("游戏窗口状态已变化，请在路线显示后重试。", 4000)
            return
        if not self.overlay.set_capture_excluded(False):
            self.end_screenshot_mode()
            self.statusBar().showMessage("Windows 未能开启截图模式，请重试。", 4000)
            return
        self.screenshot_pending = False
        self.last_visible = time.monotonic()
        self.screenshot_button.setText("退出截图模式")
        self.state.setText("截图模式 · 路线已冻结，可以截图")
        if self.preview_worker is not None:
            self.preview_caption.setText("截图模式 · 实时同步已暂停")

    def end_screenshot_mode(self):
        self.screenshot_pending = False
        self.screenshot_button.setChecked(False)
        self.screenshot_button.setText("截图模式")
        if not self.capture_pause.paused:
            return
        restored = self.overlay.set_capture_excluded(True)
        self.overlay.hide()
        if not restored:
            # Discard the HWND before resuming if Windows could not restore its exclusion.
            self.overlay.destroy()
        self.last_visible = 0
        self.capture_pause.set_paused(False)
        self.state.setText("截图模式已结束 · 正在恢复定位")
        if self.preview_worker is not None:
            self.preview_caption.setText("实时同步 · 等待下一帧")

    def overlay_box_matches(self, box):
        return box is not None and box == self.current_overlay_box()

    def current_overlay_box(self):
        if self.worker is None:
            return None
        current = native.client_box(self.worker.hwnd, strict=False)
        position = self.worker.position
        if current and position.matches(current[2:]):
            return position.absolute_box(current)
        return None

    def receive_frame(self, payload):
        if payload["generation"] != self.generation or self.worker is None:
            return
        match = payload["match"]
        if payload.get("fatal"):
            self.stop_tracking()
            self.state.setText(match.reason)
            return
        if self.capture_pause.paused or payload.get("capture_revision", 0) != self.capture_pause.revision:
            return
        now = time.monotonic()
        fresh = now - payload["time"] <= .35
        visible = (payload["ready"] and fresh and native.foreground_is(self.worker.hwnd) and
                   self.overlay_box_matches(payload["box"]))
        if visible:
            try:
                self.overlay.display(payload["box"], self.project, match.matrix)
                self.last_visible = payload["time"]
            except Exception as exc:
                self.stop_tracking()
                self.error(exc)
                return
            if self.screenshot_pending:
                self.begin_screenshot_mode()
                return
        else:
            self.overlay.hide()
        if payload["image"] is not None and now - self.preview_at > .25:
            self.show_match(payload["image"], match, display_matrix=visible, update_image=False)
            self.preview_at = now
        self.state.setText(match.reason if not match.ok else
                           ("实时定位 · 已显示覆盖层" if visible else
                            ("处理延迟过大，已隐藏覆盖层" if not fresh else "正在确认定位，覆盖层暂时隐藏")))
        self.update_minimap_status()

    def expire_overlay(self):
        now = time.monotonic()
        if self.capture_pause.paused:
            self.update_frozen_overlay(now)
        elif self.overlay.isVisible() and (
                now - self.last_visible > .35 or not self.overlay_box_matches(self.overlay.capture_box) or
                not native.foreground_is(self.worker.hwnd)):
            self.overlay.hide()

    def update_frozen_overlay(self, now):
        box = self.current_overlay_box()
        if box is not None and native.foreground_is(self.worker.hwnd):
            self.last_visible = now
            if not self.overlay.isVisible() or box != self.overlay.capture_box:
                try:
                    self.overlay.display_frozen(box)
                except (OSError, ValueError):
                    self.overlay.hide()
                    self.state.setText("截图模式 · 暂时无法显示路线")
                else:
                    self.state.setText("截图模式 · 路线已冻结，可以截图")
        # Give the screenshot picker time to freeze the desktop when it takes focus.
        elif self.overlay.isVisible() and (box != self.overlay.capture_box or now - self.last_visible > .5):
            self.overlay.hide()
            self.state.setText("截图模式 · 等待游戏窗口")

    def show_match(self, image, match, display_matrix=True, update_image=True):
        self.metrics.setText(f"内点 {match.inliers}/{match.candidates} · 覆盖 {match.coverage:.0%}\n"
                             f"图像拟合误差 {match.error:.2f} px · 算法 {match.elapsed_ms:.0f} ms · 搜索{match.scope}")
        self.state.setText(match.reason)
        if update_image:
            self.update_preview(image, match.matrix if display_matrix else None, "小地图匹配预览")

    def update_preview(self, image, matrix, caption=None):
        if caption is not None:
            self.preview_caption.setText(caption)
        self.last_preview = image
        self.last_preview_matrix = matrix
        rendered = qimage(image)
        if matrix is not None and self.project:
            h, w = image.shape[:2]
            with QPainter(rendered) as painter:
                paint_minimap(painter, self.project, affine_transform(inverse(matrix)), QRectF(0, 0, w, h),
                              ink_opacity=self.overlay.ink_opacity)
        self.preview.setPixmap(QPixmap.fromImage(rendered).scaled(204, 204, Qt.AspectRatioMode.KeepAspectRatio,
                                                                  Qt.TransformationMode.SmoothTransformation))

    def test_image(self):
        try:
            self.require_project()
            if not self.stop_tracking(show=False):
                return
            path = self.choose_file("选择带小地图的截图", "截图 (*.png *.jpg *.jpeg *.bmp)")
            if not path:
                return
            image = read_image(path)
            selector = RegionDialog(image, mini=True, parent=self)
            if selector.exec() != QDialogAccepted:
                return
            x, y, w, h = selector.rect
            query = image[y:y+h, x:x+w].copy()
            self.localizer.reset()
            self.show_match(query, self.localizer.locate(query))
        except Exception as exc:
            self.error(exc)

    def load_demo(self):
        if not self.confirm_discard():
            return
        from .demo import make_demo, mini_at
        project = make_demo()
        self.set_project(project)
        mini, _ = mini_at(project.image)
        self.show_match(mini, self.localizer.locate(mini))
        self.detail.setText("这是程序生成的合成地图，用于检查编辑、定位预览和保存流程。\n演示结果不代表真实游戏定位精度。要测试游戏，请建立实际区域。")

    def choose_color(self):
        if self.project:
            selected = QColorDialog.getColor(QColor(self.project.color), self, "画笔颜色",
                                             QColorDialog.ColorDialogOption.DontUseNativeDialog)
            if selected.isValid():
                self.project.color = selected.name()
                self.refresh_brush_color()
                self.annotations_changed()

    def nativeEvent(self, event_type, message):
        key = native.hotkey_message(message)
        if key and not self.busy and not self.settings_dialog.isVisible():
            # Queue actions after the native event, so modal dialogs do not nest here.
            action = {2: self.toggle_tracking,
                      native.SCREENSHOT_HOTKEY: self.toggle_screenshot_mode}.get(key)
            if action:
                QTimer.singleShot(0, action)
                return True, 0
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event):
        if not self.stop_tracking(show=False) or not self.confirm_discard():
            event.ignore()
            return
        if not self.stop_preview():
            event.ignore()
            return
        native.unregister_hotkeys(int(self.winId()), self.registered)
        self.overlay.close()
        event.accept()


QDialogAccepted = 1
