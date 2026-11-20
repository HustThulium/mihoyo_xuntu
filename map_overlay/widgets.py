from __future__ import annotations

from dataclasses import replace
import math

import cv2
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QRect
from PySide6.QtGui import (QImage, QPixmap, QPainter, QPainterPath, QPen, QColor,
                           QBrush, QGuiApplication, QTransform, QFont)
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                              QPushButton, QDialogButtonBox, QGraphicsView,
                              QGraphicsScene, QSpinBox)

from .annotations import (AnnotationEdits, Stroke, INK_WIDTH, ERASER_DIAMETER,
                          affine_transform, erase_ink, marker_rect, paint_annotations, paint_minimap)
from .geometry import inverse, similarity
from .project import DEFAULT_MARKER_SIZE, Marker
from .theme import CANVAS_BACKGROUND, workflow_layout
from . import native


def qimage(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()


def ink_size_control(label, maximum, value):
    control = QSpinBox()
    control.setRange(1, maximum)
    control.setPrefix(f"{label} ")
    control.setSuffix(" px")
    control.setAccessibleName(label)
    control.setValue(value)
    return control


def ink_checkerboard():
    tile = QPixmap(24, 24)
    tile.fill(QColor("#465058"))
    with QPainter(tile) as painter:
        for offset in (0, 12):
            painter.fillRect(offset, offset, 12, 12, QColor("#59636b"))
    return QBrush(tile)


def paint_eraser_cursor(painter, point, diameter):
    radius = diameter / 2
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for color, width in [("black", 3), ("white", 1)]:
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawEllipse(point, radius, radius)


class ImageView(QGraphicsView):
    picked = Signal(float, float)
    selected = Signal(object)

    def __init__(self, image=None, mode="view", parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor(CANVAS_BACKGROUND))
        self.setMinimumSize(160, 140)
        self.image = None
        self.start = None
        self.selection = None
        self.markers = []
        self.selection_item = None
        self.image_item = None
        self._zoomed = False
        ImageView.set_mode(self, mode)
        if image is not None:
            self.set_image(image)

    def set_image(self, image):
        self.image = image
        self.scene().clear()
        self.markers = []
        self.image_item = self.scene().addPixmap(QPixmap.fromImage(qimage(image)))
        self.scene().setSceneRect(0, 0, image.shape[1], image.shape[0])
        self.selection_item = self.scene().addRect(QRectF(), QPen(QColor("#ffce73"), 2))
        self.selection_item.hide()
        self.selection = None
        self._zoomed = False
        self.fit_image()

    def fit_image(self):
        if self.image is not None:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._zoomed = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._zoomed:
            self.fit_image()

    def wheelEvent(self, event):
        if self.image is not None:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            current = self.transform().m11()
            if .03 < current * factor < 12:
                self.scale(factor, factor)
                self._zoomed = True
        event.accept()

    def set_mode(self, mode):
        self.mode = mode
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if mode == "view" else QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.OpenHandCursor if mode == "view" else Qt.CursorShape.CrossCursor)

    def image_point(self, event):
        point = self.mapToScene(event.position().toPoint())
        if self.image is None or not self.sceneRect().contains(point):
            return None
        return [point.x(), point.y()]

    def mousePressEvent(self, event):
        point = self.image_point(event)
        if event.button() == Qt.MouseButton.LeftButton and point is not None:
            if self.mode == "pick":
                self.picked.emit(*point)
                return
            if self.mode in ("rect", "square"):
                self.start = point
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = self.image_point(event)
        if self.start is not None and point is not None:
            x0, y0 = self.start
            dx, dy = point[0] - x0, point[1] - y0
            if self.mode == "square":
                edge = min(abs(dx), abs(dy))
                dx, dy = math.copysign(edge, dx), math.copysign(edge, dy)
            rect = QRectF(QPointF(x0, y0), QPointF(x0 + dx, y0 + dy)).normalized()
            self.selection_item.setRect(rect)
            self.selection_item.show()
            self.selection = [round(rect.x()), round(rect.y()), round(rect.width()), round(rect.height())]
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.start is not None:
                self.start = None
                self.selected.emit(self.selection)
                return
        super().mouseReleaseEvent(event)

    def mark(self, points):
        for item in self.markers:
            self.scene().removeItem(item)
        self.markers = []
        for index, point in enumerate(points, 1):
            x, y = point
            dot = self.scene().addEllipse(x - 4, y - 4, 8, 8, QPen(QColor("#101820")), QBrush(QColor("#ffce73")))
            text = self.scene().addText(str(index))
            text.setDefaultTextColor(QColor("#ffce73"))
            text.setPos(x + 5, y - 18)
            self.markers.extend([dot, text])


class AnnotationView(ImageView):
    changed = Signal()
    marker_selected = Signal(object)
    placement_changed = Signal(bool)
    placement_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(mode="draw", parent=parent)
        self.project = None
        self.edits = None
        self.stroke = None
        self.ink_width = INK_WIDTH
        self.eraser_diameter = ERASER_DIAMETER
        self.erase_preview = None
        self.eraser_pointer = None
        self.ink_only = False
        self.checker = ink_checkerboard()
        self.pending_asset = None
        self.placement_size = DEFAULT_MARKER_SIZE
        self.selected_marker = None
        self.drag_start = None
        self.dragged_marker = None

    def set_project(self, project):
        self.project = project
        self.edits = AnnotationEdits(project)
        self.placement_size = DEFAULT_MARKER_SIZE
        self.set_mode(self.mode)
        self.set_image(project.image)
        self.set_ink_only(self.ink_only)

    def set_mode(self, mode):
        super().set_mode(mode)
        self.viewport().setMouseTracking(mode == "erase")
        self.stroke = None
        self.erase_preview = None
        self.eraser_pointer = None
        self.cancel_placement()
        self.select_marker(None)

    def set_ink_only(self, enabled):
        self.ink_only = enabled
        if self.image_item is not None:
            self.image_item.setVisible(not enabled)
        self.viewport().update()

    def set_ink_width(self, width):
        self.ink_width = width

    def set_eraser_diameter(self, diameter):
        self.eraser_diameter = diameter
        self.viewport().update()

    def select_marker(self, index):
        self.selected_marker = index
        self.drag_start = None
        self.dragged_marker = None
        self.marker_selected.emit(self.project.markers[index] if index is not None else None)
        self.viewport().update()

    def prepare_marker(self, asset):
        self.select_marker(None)
        self.pending_asset = asset
        self.placement_changed.emit(True)
        self.setFocus()

    def cancel_placement(self):
        if self.pending_asset is not None:
            self.pending_asset = None
            self.placement_changed.emit(False)

    def change_marker_size(self, size):
        if self.selected_marker is None:
            self.placement_size = size
            return
        marker = self.project.markers[self.selected_marker]
        if self.edits.update_marker(self.selected_marker, replace(marker, size=size)):
            self.viewport().update()
            self.changed.emit()

    def delete_marker(self):
        if self.selected_marker is not None:
            self.edits.remove_marker(self.selected_marker)
            self.select_marker(None)
            self.changed.emit()

    def undo(self):
        if self.stroke is not None:
            self.stroke = self.erase_preview = None
            self.viewport().update()
            return
        if self.edits is not None and self.edits.undo():
            self.select_marker(None)
            self.changed.emit()

    def marker_bounds(self, marker):
        return marker_rect(marker, QPointF(marker.x, marker.y), 1 / self.transform().m11())

    def drawForeground(self, painter, exposed):
        if self.project is None:
            return
        painter.save()
        painter.setClipRect(self.sceneRect(), Qt.ClipOperation.IntersectClip)
        if self.ink_only:
            painter.fillRect(exposed, self.checker)
        markers = () if self.ink_only else self.project.markers
        if not self.ink_only and self.dragged_marker is not None:
            markers = markers.copy()
            markers[self.selected_marker] = self.dragged_marker
        strokes = (self.stroke,) if self.stroke is not None and self.mode != "erase" else ()
        paint_annotations(painter, self.project, QTransform(), exposed, 1 / self.transform().m11(),
                          strokes, markers, ink_preview=self.erase_preview)
        if self.mode == "erase" and self.eraser_pointer is not None:
            diameter = self.stroke.width if self.stroke is not None else self.eraser_diameter
            paint_eraser_cursor(painter, self.eraser_pointer, diameter)
        if not self.ink_only and self.selected_marker is not None:
            pen = QPen(QColor("#ffce73"), 1, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(self.marker_bounds(markers[self.selected_marker]))
        painter.restore()

    def mousePressEvent(self, event):
        if (self.project is not None and self.mode != "view" and
                event.button() == Qt.MouseButton.LeftButton):
            point = self.mapToScene(event.position().toPoint())
            if not (0 <= point.x() < self.sceneRect().width() and 0 <= point.y() < self.sceneRect().height()):
                self.select_marker(None)
            elif self.mode in ("draw", "line"):
                self.stroke = Stroke(QPainterPath(point), self.project.color, self.ink_width)
                self.viewport().update()
            elif self.mode == "erase":
                self.eraser_pointer = point
                if self.project.ink is not None:
                    self.stroke = Stroke(QPainterPath(point), self.project.color, self.eraser_diameter)
                    self.erase_preview = QImage(self.project.ink)
                    erase_ink(self.erase_preview, self.stroke.path, self.stroke.width)
                self.viewport().update()
            elif self.mode == "marker" and not self.ink_only and self.pending_asset is not None:
                try:
                    self.edits.add_marker(Marker(self.pending_asset, point.x(), point.y(), self.placement_size))
                except ValueError as exc:
                    self.cancel_placement()
                    self.placement_failed.emit(str(exc))
                else:
                    self.viewport().update()
                    self.changed.emit()
            elif self.mode == "marker" and not self.ink_only:
                index = next((i for i in range(len(self.project.markers) - 1, -1, -1)
                              if self.marker_bounds(self.project.markers[i]).contains(point)), None)
                self.select_marker(index)
                if index is not None:
                    self.drag_start = point
            return
        super().mousePressEvent(event)

    def extend_stroke(self, point):
        stroke = self.stroke
        if self.mode == "line":
            first = stroke.path.elementAt(0)
            start = QPointF(first.x, first.y)
            stroke.path = QPainterPath(start)
            if start != point:
                stroke.path.lineTo(point)
        elif self.mode == "erase":
            previous = stroke.path.currentPosition()
            if previous != point:
                stroke.path.lineTo(point)
                segment = QPainterPath(previous)
                segment.lineTo(point)
                erase_ink(self.erase_preview, segment, stroke.width)
        else:
            stroke.add_point(point)

    def mouseMoveEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        if self.mode == "erase":
            self.eraser_pointer = point
        if self.stroke is not None:
            self.extend_stroke(point)
            self.viewport().update()
        elif self.mode == "erase":
            self.viewport().update()
        elif self.drag_start is not None:
            self.move_marker(point)
        else:
            super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.eraser_pointer is not None:
            self.eraser_pointer = None
            self.viewport().update()
        super().leaveEvent(event)

    def move_marker(self, point):
        delta = point - self.drag_start
        marker = self.project.markers[self.selected_marker]
        if delta.isNull():
            self.dragged_marker = marker
        else:
            h, w = self.project.image.shape[:2]
            x = min(w - 1, max(0, marker.x + delta.x()))
            y = min(h - 1, max(0, marker.y + delta.y()))
            self.dragged_marker = replace(marker, x=x, y=y)
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.stroke is not None:
                self.extend_stroke(point)
                stroke, self.stroke = self.stroke, None
                if self.erase_preview is not None:
                    self.eraser_pointer = point
                    preview, self.erase_preview = self.erase_preview, None
                    changed = self.edits.commit_erasure(preview, stroke.path, stroke.width)
                else:
                    changed = self.edits.draw(stroke)
                if changed:
                    self.changed.emit()
                self.viewport().update()
                return
            if self.drag_start is not None:
                self.move_marker(point)
                changed = self.edits.update_marker(self.selected_marker, self.dragged_marker)
                self.drag_start = self.dragged_marker = None
                if changed:
                    self.changed.emit()
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.stroke is not None and event.key() == Qt.Key.Key_Escape:
            self.stroke = self.erase_preview = None
            self.viewport().update()
        elif self.mode == "marker" and event.key() == Qt.Key.Key_Escape:
            self.cancel_placement()
            self.select_marker(None)
        elif self.mode == "marker" and event.key() == Qt.Key.Key_Delete:
            self.delete_marker()
        else:
            super().keyPressEvent(event)


class RegionDialog(QDialog):
    def __init__(self, image, mini=False, parent=None):
        super().__init__(parent)
        title = "框选小地图" if mini else "框选区域底图"
        self.setWindowTitle(title)
        self.resize(1120, 750)
        layout, _ = workflow_layout(self, title)
        text = ("拖出圆形小地图的外接正方形，圆心应位于框中央。避开旁边的按钮。" if mini else
                "框选清晰的地图区域，避开菜单和缩放条，给路线周围留出余量。")
        instruction = QLabel(text)
        instruction.setWordWrap(True)
        instruction.setObjectName("muted")
        layout.addWidget(instruction)
        self.view = ImageView(image, "square" if mini else "rect")
        layout.addWidget(self.view, 1)
        self.hint = QLabel("拖动选择范围；滚轮缩放。")
        self.hint.setWordWrap(True)
        footer = QHBoxLayout()
        footer.addWidget(self.hint, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("使用所选范围")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        self.view.selected.connect(self.selected)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self.rect = None
        self.minimum = 96 if mini else 200

    def selected(self, rect):
        self.rect = rect
        valid = bool(rect and min(rect[2:]) >= self.minimum)
        self.ok.setEnabled(valid)
        if valid:
            self.hint.setText(f"已选择 {rect[2]} × {rect[3]} 像素")
        else:
            self.hint.setText(f"请框选至少 {self.minimum} × {self.minimum} 像素的区域。")


class MinimapSettingsDialog(QDialog):
    """Dedicated minimap position workflow, separate from program preferences."""

    def __init__(self, status, position=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("小地图位置设置")
        self.setMinimumWidth(510)
        self.action = None
        layout, _ = workflow_layout(self, "小地图位置")
        panel = QFrame()
        panel.setObjectName("workflowPanel")
        details = QVBoxLayout(panel)
        summary = QLabel(status)
        summary.setObjectName("panelHeading")
        summary.setWordWrap(True)
        details.addWidget(summary)
        if position:
            x, y, w, h = position.rect
            details.addWidget(QLabel(f"游戏画面内的位置 ({x}, {y}) · 范围 {w} × {h} 像素"))
        layout.addWidget(panel)
        explanation = QLabel("此设置保存在本机，所有区域共用，无需先建立区域。\n"
                             "游戏窗口移动后可继续使用；分辨率、窗口大小或游戏 UI 布局改变后，请重新框选。\n"
                             "框选前请关闭游戏大地图，确保小地图完整可见。")
        explanation.setObjectName("muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.select_button = QPushButton("重新框选小地图位置" if position else "从游戏画面框选小地图位置")
        self.select_button.setObjectName("primary")
        self.select_button.setDefault(True)
        self.select_button.clicked.connect(lambda: self.choose("select"))
        actions = QHBoxLayout()
        actions.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        actions.addWidget(self.select_button)
        layout.addLayout(actions)

    def choose(self, action):
        self.action = action
        self.accept()


class CalibrationDialog(QDialog):
    def __init__(self, reference, query, parent=None):
        super().__init__(parent)
        self.setWindowTitle("辅助校准 · 选择同一组地标")
        self.resize(1120, 730)
        self.source, self.destination = [], []
        self.matrix = None
        layout, _ = workflow_layout(self, "配对地图地标")
        self.hint = QLabel("先点左侧底图中的地标 1，再点右侧截图中的同一地标。至少三组，尽量分散。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        self.reference = ImageView(reference, "pick")
        self.query = ImageView(query, "pick")
        for title, view in [("保存的底图", self.reference), ("当前截图", self.query)]:
            column = QVBoxLayout()
            heading = QLabel(title)
            heading.setObjectName("panelHeading")
            column.addWidget(heading)
            column.addWidget(view, 1)
            row.addLayout(column, 1)
        layout.addLayout(row, 1)
        actions = QHBoxLayout()
        undo = QPushButton("撤销一组")
        undo.clicked.connect(self.undo)
        actions.addWidget(undo)
        actions.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("使用校准结果")
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self.reference.picked.connect(self.pick_reference)
        self.query.picked.connect(self.pick_query)

    def pick_reference(self, x, y):
        if len(self.destination) == len(self.source):
            self.destination.append([x, y])
            self.reference.mark(self.destination)
            self.hint.setText(f"请在右侧截图选择同一个地标 {len(self.destination)}。")
            self.ok.setEnabled(False)

    def pick_query(self, x, y):
        if len(self.destination) == len(self.source) + 1:
            self.source.append([x, y])
            self.query.mark(self.source)
            self.validate()

    def undo(self):
        if len(self.destination) > len(self.source):
            self.destination.pop()
        elif self.source:
            self.source.pop()
            self.destination.pop()
        self.reference.mark(self.destination)
        self.query.mark(self.source)
        self.validate()

    def validate(self):
        self.ok.setEnabled(False)
        self.matrix = None
        self.hint.setText(f"已选择 {len(self.source)} 组。继续在左侧选择下一处地标；至少需要三组。")
        if len(self.source) >= 3:
            try:
                matrix, error = similarity(self.destination, self.source)  # residual in query pixels
                if error > 5:
                    raise ValueError(f"对应误差 {error:.1f} 像素过大，请撤销并检查地标。")
                self.matrix = inverse(matrix)
                self.ok.setEnabled(True)
                self.hint.setText(f"已选 {len(self.source)} 组，拟合误差 {error:.1f} 像素。可以继续加点或确认。")
            except ValueError as exc:
                self.hint.setText(str(exc))


def logical_geometry(box):
    device, physical, monitor = native.monitor_for(box, with_handle=True)
    screens = QGuiApplication.screens()
    # Recent Qt versions expose friendly monitor names, not Win32 DISPLAY names.
    # Compare the actual HMONITOR rather than assuming those strings match.
    screen = None
    for candidate in screens:
        try:
            if int(candidate.nativeInterface().handle()) == monitor:
                screen = candidate
                break
        except (AttributeError, TypeError):
            if candidate.name().lower() == device.lower():
                screen = candidate
    if screen is None:
        if len(screens) != 1:
            raise ValueError("无法对应显示器，请将游戏放在主显示器后重试。")
        screen = screens[0]
    ratio = screen.devicePixelRatio()
    origin = screen.geometry().topLeft()
    x, y, w, h = box
    return QRect(round(origin.x() + (x - physical[0]) / ratio),
                 round(origin.y() + (y - physical[1]) / ratio),
                 round(w / ratio), round(h / ratio)), ratio


class RouteOverlay(QWidget):
    def __init__(self, ink_opacity=1.0):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.project = None
        self.matrix = None
        self.pixel_size = (1, 1)
        self.pixel_ratio = 1.0
        self.ink_opacity = ink_opacity
        self.capture_box = None

    def display(self, box, project, query_to_ref):
        self.position_at(box)
        self.project = project
        self.matrix = affine_transform(inverse(query_to_ref))
        if not self.isVisible():
            if not self.set_capture_excluded(True):
                raise OSError("系统无法将叠加层排除出截屏，已停止显示，避免识别到覆盖内容自身。")
            self.show()
        self.update()

    def display_frozen(self, box):
        self.position_at(box)
        if not self.isVisible():
            if not self.set_capture_excluded(False):
                raise OSError("Windows 未能重新显示可截图的叠加层。")
            self.show()
        self.update()

    def position_at(self, box):
        geometry, self.pixel_ratio = logical_geometry(box)
        self.setGeometry(geometry)
        self.pixel_size = box[2:]
        self.capture_box = tuple(box)

    def set_capture_excluded(self, excluded):
        return native.set_capture_excluded(int(self.winId()), excluded)

    def paintEvent(self, _):
        if self.project is None:
            return
        w, h = self.pixel_size
        frame = QRectF(0, 0, w, h)
        with QPainter(self) as painter:
            painter.scale(self.width() / w, self.height() / h)
            paint_minimap(painter, self.project, self.matrix, frame, self.pixel_ratio, self.ink_opacity)
            self.paint_watermark(painter, frame)

    def paint_watermark(self, painter, frame):
        size = min(frame.width(), frame.height())
        font = QFont("Microsoft YaHei UI")
        font.setWeight(QFont.Weight.DemiBold)
        font.setPixelSize(max(1, round(min(size * .125, 21 * self.pixel_ratio))))
        text = QPainterPath()
        text.addText(0, 0, font, "循图墨迹层")
        bounds = text.boundingRect()
        center = frame.center()
        top = center.y() - size / 2
        text.translate(center.x() - bounds.center().x(), top + size * .23 - bounds.center().y())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPen(QColor(0, 0, 0, 80), 1.3 * self.pixel_ratio)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(text, outline)
        painter.fillPath(text, QColor(255, 255, 255, 107))
