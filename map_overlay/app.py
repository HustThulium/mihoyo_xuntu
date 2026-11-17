from __future__ import annotations

import copy
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import Qt, QTimer, QStandardPaths
from PySide6.QtGui import QPainter, QPixmap, QKeySequence, QShortcut, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                              QVBoxLayout, QPushButton, QLabel, QFileDialog,
                              QMessageBox, QComboBox, QSplitter, QFrame,
                              QCheckBox, QInputDialog, QColorDialog)

from . import native
from .capture import TrackingWorker, PreviewWorker, grab
from .geometry import inverse
from .project import Project, read_image
from .settings import MinimapPosition, MinimapSettings
from .settings_ui import SettingsDialog
from .vision import Localizer
from .widgets import (ImageView, RegionDialog, CalibrationDialog, DrawingOverlay,
                      RouteOverlay, MinimapSettingsDialog, qimage, paint_routes)


STYLE = """
QWidget { background:#111a23; color:#dce8ef; font-family:'Microsoft YaHei UI'; font-size:13px; }
QMainWindow { background:#0c131a; }
QLabel#title { font-size:25px; font-weight:700; color:#f1faf8; }
QLabel#muted { color:#91a6b6; }
QLabel#section { color:#65e7bd; font-size:12px; font-weight:700; margin-top:12px; }
QPushButton { background:#1a2a37; border:1px solid #2b4050; border-radius:7px; padding:9px 12px; text-align:left; }
QPushButton:hover { background:#223b49; border-color:#4b9e8d; }
QPushButton:disabled { color:#526574; border-color:#22313d; }
QPushButton#primary { background:#22745f; border-color:#32987d; color:white; font-weight:600; }
QPushButton#primary:hover { background:#2d8c73; }
QPushButton#settingsButton { padding:0; text-align:center; font-family:'Segoe UI Symbol'; font-size:21px; }
QComboBox { background:#1a2a37; border:1px solid #2b4050; border-radius:6px; padding:7px; }
QComboBox QAbstractItemView { background:#1a2a37; selection-background-color:#22745f; }
QCheckBox { spacing:8px; padding:4px; }
QGraphicsView { border:1px solid #2b4050; border-radius:7px; }
QSplitter::handle { background:#243644; }
QStatusBar { color:#a8bdc9; background:#0c131a; }
QToolTip { background:#253b49; color:white; border:1px solid #4b9e8d; }
"""


class MainWindow(QMainWindow):
    def __init__(self, settings_path=None):
        super().__init__()
        self.setWindowTitle("循图 · 原神路线叠加原型 0.1")
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
        self.overlay = RouteOverlay()
        self.last_preview = None
        self.last_preview_matrix = None
        self.minimap_preview = None
        self.preview_at = 0
        if settings_path is None:
            settings_path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "settings.json"
        self.minimap_settings = MinimapSettings(settings_path)
        self.build_ui()
        self.refresh_windows()
        self.registered = native.register_hotkeys(int(self.winId()))
        self.statusBar().showMessage("仅本地截图与画面叠加 · 不读取游戏内存 · 不注入 · 不自动操作游戏")
        if len(self.registered) != 3:
            self.statusBar().showMessage("部分全局快捷键被占用，请使用界面按钮；快捷键冲突不影响画线和保存。")
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self.expire_overlay)
        self.watchdog.start(60)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_project)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.canvas.undo)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(18)
        sidebar = QWidget()
        sidebar.setFixedWidth(288)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(4)
        title = QLabel("循图  /  ROUTE")
        title.setObjectName("title")
        side.addWidget(title)
        subtitle = QLabel("局部地图 · 手绘路线 · 视觉定位\nWindows 画面叠加验证原型")
        subtitle.setObjectName("muted")
        side.addWidget(subtitle)

        def section(text):
            label = QLabel(text)
            label.setObjectName("section")
            side.addWidget(label)

        def button(text, callback, primary=False):
            control = QPushButton(text)
            if primary:
                control.setObjectName("primary")
            control.clicked.connect(callback)
            side.addWidget(control)
            return control

        section("01  选择游戏窗口")
        row = QHBoxLayout()
        self.targets = QComboBox()
        self.targets.setMinimumWidth(0)
        self.targets.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.targets.setMinimumContentsLength(12)
        self.targets.currentIndexChanged.connect(self.target_changed)
        row.addWidget(self.targets, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_windows)
        row.addWidget(refresh)
        side.addLayout(row)
        section("小地图位置")
        self.minimap_position_button = button("小地图位置设置…", self.open_minimap_settings)
        self.minimap_status = QLabel()
        self.minimap_status.setObjectName("muted")
        self.minimap_status.setWordWrap(True)
        side.addWidget(self.minimap_status)
        section("02  准备区域与路线")
        button("从游戏大地图建立区域", self.new_capture, True)
        row = QHBoxLayout()
        for text, callback in [("导入底图", self.import_reference), ("打开区域", self.open_project)]:
            item = QPushButton(text)
            item.clicked.connect(callback)
            row.addWidget(item)
        side.addLayout(row)
        button("在游戏地图上画线  Ctrl+Alt+F8", self.draw_on_game)
        row = QHBoxLayout()
        for text, callback in [("保存区域", self.save_project), ("撤销笔画", lambda: self.canvas.undo())]:
            item = QPushButton(text)
            item.clicked.connect(callback)
            row.addWidget(item)
        side.addLayout(row)
        section("03  小地图定位与叠加")
        button("辅助校准：选择对应地标", self.calibrate_minimap)
        self.track_button = button("开始叠加  Ctrl+Alt+F9", self.toggle_tracking, True)
        button("停止并返回  Ctrl+Alt+F10", self.stop_tracking)
        section("离线验证")
        button("测试一张小地图截图", self.test_image)
        button("加载离线演示（合成地图）", self.load_demo)
        note = QLabel("首版范围：地表地图、固定朝北。\n地图内缩放变化与地下分层暂不保证。\n定位不可靠时自动隐藏路线。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        side.addWidget(note)
        side.addStretch()
        self.settings_button = QPushButton("⚙")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setFixedSize(34, 34)
        self.settings_button.setToolTip("设置")
        self.settings_button.setAccessibleName("设置")
        self.settings_button.clicked.connect(self.open_settings)
        side.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(sidebar)

        content = QVBoxLayout()
        top = QHBoxLayout()
        self.area_label = QLabel("还没有区域 · 从游戏截取或导入一张底图")
        self.area_label.setStyleSheet("font-size:16px; font-weight:600;")
        top.addWidget(self.area_label, 1)
        self.draw_toggle = QCheckBox("在底图上画线")
        self.draw_toggle.setChecked(True)
        self.draw_toggle.toggled.connect(lambda checked: self.canvas.set_mode("draw" if checked else "view"))
        top.addWidget(self.draw_toggle)
        color_button = QPushButton("线色")
        color_button.clicked.connect(self.choose_color)
        top.addWidget(color_button)
        fit = QPushButton("适应窗口")
        fit.clicked.connect(lambda: self.canvas.fit_image())
        top.addWidget(fit)
        content.addLayout(top)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.canvas = ImageView(mode="draw")
        self.canvas.changed.connect(self.routes_changed)
        splitter.addWidget(self.canvas)
        diagnostics = QWidget()
        row = QHBoxLayout(diagnostics)
        row.setContentsMargins(0, 8, 0, 0)
        self.preview = QLabel("小地图识别预览")
        self.preview.setFixedSize(210, 210)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background:#0c131a; border:1px solid #2b4050; border-radius:8px;")
        preview_column = QVBoxLayout()
        preview_column.addWidget(self.preview)
        self.preview_caption = QLabel("小地图预览")
        self.preview_caption.setObjectName("muted")
        self.preview_caption.setFixedWidth(210)
        self.preview_caption.setWordWrap(True)
        self.preview_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_column.addWidget(self.preview_caption)
        row.addLayout(preview_column)
        info = QVBoxLayout()
        self.state = QLabel("等待准备地图")
        self.state.setStyleSheet("font-size:19px; font-weight:600; color:#65e7bd;")
        self.state.setWordWrap(True)
        info.addWidget(self.state)
        self.metrics = QLabel("匹配点 —    图像拟合误差 —    处理时间 —")
        self.metrics.setWordWrap(True)
        self.metrics.setObjectName("muted")
        info.addWidget(self.metrics)
        self.detail = QLabel("左键拖动画线；取消勾选后可拖动浏览，滚轮缩放。\n在游戏地图上绘制时：Enter 确认，Esc 取消，Ctrl+Z 撤销。\n可在设置中选择开始叠加后是否自动最小化；切出游戏会立即隐藏路线。")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        info.addWidget(self.detail)
        info.addStretch()
        row.addLayout(info, 1)
        splitter.addWidget(diagnostics)
        splitter.setSizes([490, 220])
        content.addWidget(splitter, 1)
        outer.addLayout(content, 1)
        self.settings_dialog = SettingsDialog(
            self.minimap_settings.preview_enabled, parent=self,
            minimize_on_tracking=self.minimap_settings.minimize_on_tracking)
        self.preview_toggle = self.settings_dialog.preview_toggle
        self.preview_toggle.toggled.connect(self.toggle_preview)
        self.settings_dialog.minimize_toggle.toggled.connect(self.toggle_minimize_on_tracking)

    def error(self, exception):
        QMessageBox.warning(self, "无法完成操作", str(exception))

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
        status = self.minimap_settings.status(self.current_client_size())
        self.minimap_status.setText(status)

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
        self.preview_worker = PreviewWorker(target, position, self.preview_generation)
        self.preview_worker.result.connect(self.receive_preview)
        self.preview_worker.start()

    def receive_preview(self, payload):
        if payload["generation"] != self.preview_generation or self.preview_worker is None:
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
        if payload.get("fatal"):
            self.stop_preview()

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
        self.project, self.project_path = project, path
        self.seed = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.localizer = Localizer(project.image)
            self.canvas.strokes = project.strokes
            self.canvas.color = project.color
            self.canvas.set_image(project.image)
        finally:
            QApplication.restoreOverrideCursor()
        self.dirty = path is None
        self.update_label()
        self.state.setText("底图已准备 · 可以画线")
        self.metrics.setText(f"底图 {project.image.shape[1]} × {project.image.shape[0]} · 特征点 {len(self.localizer.points)}")
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
            total = sum(len(s) for s in self.project.strokes)
            self.area_label.setText(f"{self.project.name}{' *' if self.dirty else ''}  ·  {len(self.project.strokes)} 笔 / {total} 点")

    def routes_changed(self):
        if self.project:
            self.project.strokes = self.canvas.strokes
            self.dirty = True
            self.update_label()
            if self.last_preview is not None:
                self.update_preview(self.last_preview, self.last_preview_matrix)

    def require_project(self):
        if self.project is None:
            raise ValueError("请先建立区域、导入底图或打开已有区域文件。")

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

        def received(image, box):
            selector = RegionDialog(image)
            if selector.exec() != QDialogAccepted:
                return
            x, y, w, h = selector.rect
            selected = image[y:y+h, x:x+w].copy()
            name, ok = QInputDialog.getText(None, "区域名称", "给这个跑图区域起一个名字：", text="我的收集路线")
            if not ok:
                return
            project = Project(selected, name.strip() or "未命名区域")
            project.target_title = self.targets.currentText()
            self.set_project(project)
            absolute = (box[0] + x, box[1] + y, w, h)
            self.edit_capture(selected, absolute, np.array([[1., 0., 0.], [0., 1., 0.]]))

        self.capture_action(received)

    def edit_capture(self, image, box, matrix):
        editor = DrawingOverlay(image, box, self.project.strokes, matrix, self.project.color)
        if editor.exec() == QDialogAccepted:
            self.project.strokes = editor.strokes
            self.canvas.strokes = self.project.strokes
            self.canvas.refresh_routes()
            self.routes_changed()

    def draw_on_game(self):
        if self.busy:
            return
        try:
            self.require_project()
        except ValueError as exc:
            self.error(exc)
            return

        def received(image, box):
            selector = RegionDialog(image)
            selector.setWindowTitle("框选当前可见地图 · 识别后继续画线")
            if selector.exec() != QDialogAccepted:
                return
            x, y, w, h = selector.rect
            query = image[y:y+h, x:x+w].copy()
            match = self.localizer.locate(query, mini=False)
            matrix = match.matrix
            if matrix is None:
                dialog = CalibrationDialog(self.project.image, query)
                if dialog.exec() != QDialogAccepted:
                    return
                matrix = dialog.matrix
            self.edit_capture(query, (box[0] + x, box[1] + y, w, h), matrix)

        self.capture_action(received)

    def import_reference(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入清晰的局部地图", "", "地图图片 (*.png *.jpg *.jpeg *.bmp)")
        if path:
            try:
                self.set_project(Project(read_image(path), Path(path).stem))
            except Exception as exc:
                self.error(exc)

    def open_project(self, path=None):
        if not self.confirm_discard():
            return
        if not path or isinstance(path, bool):
            path, _ = QFileDialog.getOpenFileName(self, "打开区域", "", "循图区域 (*.routepack)")
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
                path, _ = QFileDialog.getSaveFileName(self, "保存区域与路线", self.project.name + ".routepack", "循图区域 (*.routepack)")
            if not path:
                return False
            if not str(path).lower().endswith(".routepack"):
                path += ".routepack"
            self.project.save(path)
            self.project_path = path
            self.dirty = False
            self.update_label()
            self.statusBar().showMessage(f"已保存：{path}")
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
            return
        try:
            accepted = dialog.exec() == QDialogAccepted
        finally:
            self.busy = False
            self.sync_preview()
        if accepted and dialog.action == "select":
            self.select_minimap()

    def save_minimap_position(self, position, preview_image):
        if not self.stop_tracking(show=False):
            return False
        self.minimap_settings.save_position(position)
        self.seed = None
        if self.localizer:
            self.localizer.reset()
        self.minimap_preview = preview_image.copy()
        self.live_preview_image = None
        self.update_preview(self.minimap_preview, None, "位置设置成功 · 框选截图")
        self.update_minimap_status()
        self.state.setText("小地图位置设置成功 · 已保存")
        w, h = position.rect[2:]
        self.metrics.setText(f"小地图范围 {w} × {h} · 所有区域共用")
        self.detail.setText("左侧预览显示本次框选的小地图截图，可直接检查选框是否正确。\n"
                            "需要调整时，请重新打开“小地图位置设置”。")
        self.statusBar().showMessage("小地图位置已自动保存，重启或切换区域后可继续使用。")
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
            if not self.project.strokes:
                raise ValueError("请先画一条路线，再开始叠加。")
            self.generation += 1
            self.worker = TrackingWorker(self.project.image, target, position, self.generation, self.seed)
            self.worker.result.connect(self.receive_frame)
            self.worker.start()
            self.track_button.setText("停止叠加  Ctrl+Alt+F9")
            self.state.setText("正在建立实时定位…")
            if self.minimap_settings.minimize_on_tracking:
                self.showMinimized()
            native.focus(target)
        except Exception as exc:
            self.error(exc)

    def stop_tracking(self, checked=False, show=True):
        self.overlay.hide()
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
        self.track_button.setText("开始叠加  Ctrl+Alt+F9")
        if show:
            self.restore_window()
        return True

    def receive_frame(self, payload):
        if payload["generation"] != self.generation or self.worker is None:
            return
        match = payload["match"]
        now = time.monotonic()
        fresh = now - payload["time"] <= .35
        visible = (payload["ready"] and fresh and native.foreground_is(self.worker.hwnd))
        if visible:
            current = native.client_box(self.worker.hwnd, strict=False)
            x, y, w, h = self.worker.rect
            visible = bool(current and current[2:] == self.worker.client_size and
                           payload["box"] == (current[0]+x, current[1]+y, w, h))
        if visible:
            try:
                self.overlay.display(payload["box"], self.project.strokes, match.matrix, self.project.color)
                self.last_visible = payload["time"]
            except Exception as exc:
                self.stop_tracking()
                self.error(exc)
                return
        else:
            self.overlay.hide()
        if payload["image"] is not None and now - self.preview_at > .25:
            self.show_match(payload["image"], match, display_matrix=visible, update_image=False)
            self.preview_at = now
        self.state.setText(match.reason if not match.ok else
                           ("实时定位 · 路线已叠加" if visible else
                            ("处理延迟过大，已隐藏路线" if not fresh else "正在确认定位，路线暂时隐藏")))
        if payload.get("fatal"):
            reason = match.reason
            self.stop_tracking()
            self.state.setText(reason)
        self.update_minimap_status()

    def expire_overlay(self):
        if self.overlay.isVisible():
            valid = bool(self.worker and time.monotonic() - self.last_visible <= .35 and
                         native.foreground_is(self.worker.hwnd))
            if valid:
                current = native.client_box(self.worker.hwnd, strict=False)
                x, y, w, h = self.worker.rect
                if current is None or current[2:] != self.worker.client_size:
                    valid = False
                elif self.overlay.capture_box != (current[0]+x, current[1]+y, w, h):
                    valid = False
            if not valid:
                self.overlay.hide()

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
            painter = QPainter(rendered)
            # Match the visible minimap clip rather than painting on HUD corners.
            from PySide6.QtGui import QPainterPath
            from PySide6.QtCore import QPointF
            clip = QPainterPath()
            h, w = image.shape[:2]
            clip.addEllipse(QPointF(w/2, h/2), min(w, h)*.445, min(w, h)*.445)
            painter.setClipPath(clip)
            paint_routes(painter, self.project.strokes, inverse(matrix), self.project.color)
            painter.end()
        self.preview.setPixmap(QPixmap.fromImage(rendered).scaled(204, 204, Qt.AspectRatioMode.KeepAspectRatio,
                                                                  Qt.TransformationMode.SmoothTransformation))

    def test_image(self):
        try:
            self.require_project()
            if not self.stop_tracking(show=False):
                return
            path, _ = QFileDialog.getOpenFileName(self, "选择带小地图的截图", "", "截图 (*.png *.jpg *.jpeg *.bmp)")
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
            selected = QColorDialog.getColor(QColor(self.project.color), self, "路线颜色")
            if selected.isValid():
                self.project.color = selected.name()
                self.canvas.color = selected.name()
                self.canvas.refresh_routes()
                self.routes_changed()

    def nativeEvent(self, event_type, message):
        key = native.hotkey_message(message)
        if key and not self.busy and not self.settings_dialog.isVisible():
            # Queue actions after the native event, so modal dialogs do not nest here.
            action = {1: self.draw_on_game, 2: self.toggle_tracking, 3: self.stop_tracking}.get(key)
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
