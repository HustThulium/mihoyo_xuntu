from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPolygonF, QShortcut, QTransform)
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                              QSlider, QStackedWidget, QVBoxLayout, QWidget)

from .annotations import ERASER_DIAMETER, MAX_ERASER_DIAMETER, erase_ink, paint_markers
from .geometry import transform_points
from .theme import segmented_buttons, workflow_layout
from .widgets import ImageView, ink_checkerboard, ink_size_control, paint_eraser_cursor


class RouteAreaDialog(QDialog):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择路线图范围")
        self.resize(960, 720)
        layout, _ = workflow_layout(self, "选择路线图范围", step="导入路线图 · 步骤 1 / 4")
        instruction = QLabel("可使用整图，或框选路线和周围地形。拼图中的各幅地图可分次导入。")
        instruction.setObjectName("muted")
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        self.view = ImageView(source, "rect")
        layout.addWidget(self.view, 1)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.hint = QLabel("拖动框选；滚轮缩放。")
        self.hint.setWordWrap(True)
        row.addWidget(self.hint, 1)
        for label, callback in [("取消导入", self.reject), ("匹配整图", self.use_all), ("匹配选区", self.accept)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        self.crop_button = button
        self.crop_button.setObjectName("primary")
        self.crop_button.setEnabled(False)
        self.view.selected.connect(self.selection_changed)
        self.rect = None

    def selection_changed(self, rect):
        self.rect = rect
        self.crop_button.setEnabled(bool(rect and min(rect[2:]) >= 64))
        if rect and min(rect[2:]) < 64:
            self.hint.setText("请框选至少 64 × 64 像素，并包含周围地形。")
        elif rect:
            self.hint.setText(f"已选择 {rect[2]} × {rect[3]} 像素")
        else:
            self.hint.setText("拖动框选；滚轮缩放。")

    def use_all(self):
        self.rect = None
        self.view.selection = None
        self.view.selection_item.hide()
        self.crop_button.setEnabled(False)
        self.accept()


class RouteMatchDialog(QDialog):
    def __init__(self, selected, reference, match, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入路线图 · 确认匹配位置")
        self.resize(1100, 700)
        self.reselect = False
        layout, _ = workflow_layout(self, "核对匹配位置", step="导入路线图 · 步骤 2 / 4")
        images = QHBoxLayout()
        images.setSpacing(12)
        layout.addLayout(images, 1)
        views = []
        for label, image in [("路线图选区", selected), ("当前区域底图", reference)]:
            column = QVBoxLayout()
            images.addLayout(column, 1)
            heading = QLabel(label)
            heading.setObjectName("panelHeading")
            column.addWidget(heading)
            view = ImageView(image)
            column.addWidget(view, 1)
            views.append(view)
        if match is None:
            message = "未找到可靠的匹配位置。可返回框选同一区域，并保留路线附近的地形。"
        else:
            h, w = selected.shape[:2]
            outline = match.route_clip
            if outline is None:
                outline = np.float32([(0, 0), (w, 0), (w, h), (0, h)])
                message = "请核对两图中的地形。"
            else:
                message = "仅局部匹配。继续将只提取轮廓内的墨迹，也可返回重新框选。"
            points = match.route_landmarks
            self.mark(views[0], points, outline)
            self.mark(views[1], transform_points(points, match.matrix), transform_points(outline, match.matrix))
            message += f"  绿色点为对应地标（{match.inliers} 个），金色轮廓为提取范围；仅保留与底图重叠的部分。"
        self.hint = QLabel(message)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        cancel = QPushButton("取消导入")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        back = QPushButton("返回选区")
        back.clicked.connect(self.choose_area)
        buttons.addWidget(back)
        self.next = QPushButton("确认位置并继续")
        self.next.setObjectName("primary")
        self.next.setEnabled(match is not None)
        self.next.clicked.connect(self.accept)
        buttons.addWidget(self.next)

    @staticmethod
    def mark(view, points, outline):
        edge = QPen(QColor("#ffca68"), 2)
        edge.setCosmetic(True)
        polygon = QPolygonF([QPointF(*p) for p in outline])
        shadow = QPen(QColor("#302712"), 4)
        shadow.setCosmetic(True)
        view.scene().addPolygon(polygon, shadow)
        view.scene().addPolygon(polygon, edge, QBrush(QColor(255, 202, 104, 24)))
        # Distribute dots over the evidence instead of drawing thousands of
        # markers over the same small features. Keep visible gaps in coverage.
        cell = max(1, max(view.image.shape[:2]) / 100)
        _, indices = np.unique(np.floor(points / cell), axis=0, return_index=True)
        dots = QPainterPath()
        radius = max(1.0, cell * .22)
        for point in points[indices]:
            dots.addEllipse(QPointF(*point), radius, radius)
        view.scene().addPath(dots, QPen(Qt.PenStyle.NoPen), QBrush(QColor("#3ce9af")))

    def choose_area(self):
        self.reselect = True
        self.reject()


class InkPreview(ImageView):
    def __init__(self, extraction, project):
        self.ink = None
        self.ink_only = False
        self.diameter = ERASER_DIAMETER
        self.pointer = None
        self.path = None
        self.actions = []
        self.extraction = extraction
        self.project = project
        self.origin = QPointF(*extraction.origin)
        self.sensitivity = 65
        super().__init__(project.image)
        self.viewport().setMouseTracking(True)
        self.checker = ink_checkerboard()
        self.rebuild()

    def fit_import(self):
        self.fitInView(QRectF(self.ink.rect()).translated(self.origin), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoomed = True

    @property
    def cleaned(self):
        return any(path is None for path, _ in self.actions)

    def rebuild(self):
        self.ink = self.extraction.image(self.sensitivity, self.cleaned)
        for path, diameter in self.actions:
            if path is not None:
                erase_ink(self.ink, path, diameter)
        self.viewport().update()

    def clean(self):
        if not self.cleaned:
            self.actions.append((None, 0))
            self.rebuild()

    def undo(self):
        if self.actions:
            self.actions.pop()
            self.rebuild()

    def drawForeground(self, painter, rect):
        if self.ink is None:
            return
        painter.save()
        painter.setClipRect(self.sceneRect(), Qt.ClipOperation.IntersectClip)
        if self.ink_only:
            painter.fillRect(rect, self.checker)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self.ink_only and self.project.ink is not None:
            painter.drawImage(0, 0, self.project.ink)
        painter.drawImage(self.origin, self.ink)
        if not self.ink_only:
            paint_markers(painter, self.project.markers, QTransform(), rect, 1 / self.transform().m11())
        if self.mode == "erase" and self.pointer is not None:
            paint_eraser_cursor(painter, self.pointer, self.diameter)
        painter.restore()

    def mousePressEvent(self, event):
        if self.mode == "erase" and event.button() == Qt.MouseButton.LeftButton and self.image_point(event):
            self.pointer = self.mapToScene(event.position().toPoint())
            self.path = QPainterPath(self.pointer - self.origin)
            erase_ink(self.ink, self.path, self.diameter)
            self.viewport().update()
            return
        super().mousePressEvent(event)

    def extend_stroke(self, point):
        point = point - self.origin
        previous = self.path.currentPosition()
        if previous != point:
            self.path.lineTo(point)
            segment = QPainterPath(previous)
            segment.lineTo(point)
            erase_ink(self.ink, segment, self.diameter)

    def mouseMoveEvent(self, event):
        self.pointer = self.mapToScene(event.position().toPoint())
        if self.path is not None:
            self.extend_stroke(self.pointer)
        elif self.mode != "erase":
            super().mouseMoveEvent(event)
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if self.path is not None and event.button() == Qt.MouseButton.LeftButton:
            self.extend_stroke(self.mapToScene(event.position().toPoint()))
            radius = self.diameter / 2
            bounds = self.path.boundingRect().adjusted(-radius, -radius, radius, radius)
            if bounds.intersects(QRectF(self.ink.rect())):
                self.actions.append((self.path, self.diameter))
            self.path = None
            self.viewport().update()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self.pointer = None
        self.viewport().update()
        super().leaveEvent(event)


class RoutePreviewDialog(QDialog):
    def __init__(self, extraction, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入路线图 · 墨迹预览")
        self.resize(1000, 740)
        self.reselect = False
        layout, self.heading = workflow_layout(self, "调节墨迹提取")
        self.step = QLabel()
        self.step.setObjectName("dialogStep")
        layout.insertWidget(0, self.step)
        self.view = InkPreview(extraction, project)
        layout.addWidget(self.view, 1)
        modes = QHBoxLayout()
        layout.addLayout(modes)
        scope, (self.full_scope, self.match_scope) = segmented_buttons(("全图", "匹配区域"), "显示范围")
        display, (self.composite_mode, self.ink_mode) = segmented_buttons(("叠加底图", "只看本次墨迹"), "显示内容")
        modes.addWidget(QLabel("显示范围"))
        modes.addWidget(scope)
        modes.addSpacing(10)
        modes.addWidget(QLabel("显示内容"))
        modes.addWidget(display)
        self.full_scope.clicked.connect(self.view.fit_image)
        self.match_scope.clicked.connect(self.view.fit_import)
        self.ink_mode.toggled.connect(self.switch_view)
        modes.addStretch(1)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        adjust = QWidget()
        row = QHBoxLayout(adjust)
        row.setContentsMargins(0, 2, 0, 2)
        row.addWidget(QLabel("只留明显标注"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(self.view.sensitivity)
        self.slider.setAccessibleName("提取灵敏度")
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(60)
        self.refresh_timer.timeout.connect(self.refresh)
        self.slider.valueChanged.connect(lambda: self.refresh_timer.start())
        row.addWidget(self.slider, 1)
        row.addWidget(QLabel("保留更多细节"))
        self.pages.addWidget(adjust)
        cleanup = QWidget()
        row = QHBoxLayout(cleanup)
        row.setContentsMargins(0, 2, 0, 2)
        self.auto_clean = QPushButton("自动清理")
        self.auto_clean.clicked.connect(self.clean)
        row.addWidget(self.auto_clean)
        self.eraser = QCheckBox("橡皮擦")
        self.eraser.setChecked(True)
        self.eraser.toggled.connect(self.set_tool)
        row.addWidget(self.eraser)
        self.size = ink_size_control("直径", MAX_ERASER_DIAMETER, self.view.diameter)
        self.size.valueChanged.connect(self.set_diameter)
        row.addWidget(self.size)
        undo = QPushButton("撤销清理 / 擦除")
        undo.clicked.connect(self.undo)
        row.addWidget(undo)
        row.addStretch()
        self.pages.addWidget(cleanup)
        footer = QHBoxLayout()
        layout.addLayout(footer)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setObjectName("muted")
        footer.addWidget(self.message, 1)
        self.back = QPushButton()
        self.back.clicked.connect(self.previous)
        footer.addWidget(self.back)
        self.next = QPushButton()
        self.next.clicked.connect(self.advance)
        self.next.setObjectName("primary")
        footer.addWidget(self.next)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        self.set_step(0)

    def switch_view(self, checked):
        self.view.ink_only = checked
        self.view.viewport().update()

    def refresh(self):
        self.refresh_timer.stop()
        if self.view.sensitivity != self.slider.value():
            self.view.sensitivity = self.slider.value()
            self.view.rebuild()

    def set_diameter(self, value):
        self.view.diameter = value
        self.view.viewport().update()

    def set_tool(self):
        self.view.set_mode("erase" if self.pages.currentIndex() == 1 and self.eraser.isChecked() else "view")

    def set_step(self, page):
        self.pages.setCurrentIndex(page)
        self.step.setText(f"导入路线图 · 步骤 {page + 3} / 4")
        self.heading.setText("清理路线墨迹" if page else "调节墨迹提取")
        self.back.setText("返回调节" if page else ("重新选区并重置草稿" if self.view.actions else "重新选区"))
        self.next.setText("保存墨迹" if page else "下一步 · 清理")
        self.message.setText("滚轮缩放，关闭橡皮擦可拖动地图。" if page else "检查细线和点位边缘是否完整。")
        self.set_tool()

    def clean(self):
        self.view.clean()
        self.auto_clean.setEnabled(False)
        self.message.setText("已清理孤立的低色差碎点，可撤销。")

    def undo(self):
        self.view.undo()
        self.auto_clean.setEnabled(not self.view.cleaned)

    def previous(self):
        if self.pages.currentIndex() == 1:
            self.set_step(0)
        else:
            self.reselect = True
            self.reject()

    def advance(self):
        self.refresh()
        if self.pages.currentIndex() == 0:
            self.set_step(1)
            return
        alpha = self.view.ink.convertToFormat(QImage.Format.Format_Alpha8)
        data = np.frombuffer(alpha.constBits(), np.uint8).reshape(alpha.height(), alpha.bytesPerLine())
        if not np.any(data[:, :alpha.width()]):
            self.message.setText("没有可保存的墨迹。请返回调高灵敏度，或撤销擦除。")
            return
        self.accept()
