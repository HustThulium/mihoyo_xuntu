from __future__ import annotations

import copy
import math

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QRect
from PySide6.QtGui import (QImage, QPixmap, QPainter, QPainterPath, QPen, QColor,
                           QBrush, QKeySequence, QShortcut, QGuiApplication)
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QDialogButtonBox, QGraphicsView,
                              QGraphicsScene, QGraphicsPathItem)

from .geometry import inverse, similarity, transform_points
from . import native


def qimage(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()


def route_path(strokes, matrix=None):
    path = QPainterPath()
    for stroke in strokes:
        points = transform_points(stroke, matrix) if matrix is not None else np.asarray(stroke)
        if len(points) < 2:
            continue
        path.moveTo(float(points[0, 0]), float(points[0, 1]))
        for x, y in points[1:]:
            path.lineTo(float(x), float(y))
    return path


def paint_routes(painter, strokes, matrix=None, color="#63f5c3", width=3):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = route_path(strokes, matrix)
    painter.setPen(QPen(QColor(7, 21, 27, 200), width + 3, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(path)
    painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(path)


class ImageView(QGraphicsView):
    changed = Signal()
    picked = Signal(float, float)
    selected = Signal(object)

    def __init__(self, image=None, mode="view", parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor("#0c131a"))
        self.setMinimumSize(160, 140)
        self.image = None
        self.mode = mode
        self.strokes = []
        self.current = None
        self.start = None
        self.selection = None
        self.markers = []
        self.color = "#63f5c3"
        self.route_item = None
        self.selection_item = None
        self._zoomed = False
        if image is not None:
            self.set_image(image)

    def set_image(self, image):
        self.image = image
        self.scene().clear()
        self.markers = []
        self.scene().addPixmap(QPixmap.fromImage(qimage(image)))
        self.scene().setSceneRect(0, 0, image.shape[1], image.shape[0])
        self.route_item = QGraphicsPathItem()
        self.route_item.setPen(QPen(QColor(self.color), 3, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        self.scene().addItem(self.route_item)
        self.selection_item = self.scene().addRect(QRectF(), QPen(QColor("#ffce73"), 2))
        self.selection_item.hide()
        self.selection = None
        self._zoomed = False
        self.fit_image()
        self.refresh_routes()

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
            if self.mode == "draw":
                self.current = [point]
                return
            if self.mode == "pick":
                self.picked.emit(*point)
                return
            if self.mode in ("rect", "square"):
                self.start = point
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = self.image_point(event)
        if self.current is not None and point is not None:
            if np.linalg.norm(np.array(point) - self.current[-1]) >= 2:
                self.current.append(point)
                self.refresh_routes()
            return
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
            if self.current is not None:
                if len(self.current) >= 2:
                    self.strokes.append(self.current)
                    self.changed.emit()
                self.current = None
                self.refresh_routes()
                return
            if self.start is not None:
                self.start = None
                self.selected.emit(self.selection)
                return
        super().mouseReleaseEvent(event)

    def refresh_routes(self):
        if self.route_item:
            self.route_item.setPath(route_path(self.strokes + ([self.current] if self.current else [])))
            self.route_item.setPen(QPen(QColor(self.color), 3, Qt.PenStyle.SolidLine,
                                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.refresh_routes()
            self.changed.emit()

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


class RegionDialog(QDialog):
    def __init__(self, image, mini=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("框选小地图" if mini else "保存局部地图")
        self.resize(1120, 750)
        layout = QVBoxLayout(self)
        text = ("拖出圆形小地图的外接正方形，圆心应位于框中央。避开旁边的按钮。" if mini else
                "框选清晰的地图区域，避开菜单和缩放条，给路线周围留出余量。")
        layout.addWidget(QLabel(text))
        self.view = ImageView(image, "square" if mini else "rect")
        layout.addWidget(self.view, 1)
        self.hint = QLabel("拖动选择范围；滚轮缩放。")
        layout.addWidget(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("使用所选范围")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setEnabled(False)
        self.view.selected.connect(self.selected)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rect = None
        self.minimum = 96 if mini else 200

    def selected(self, rect):
        self.rect = rect
        valid = bool(rect and min(rect[2:]) >= self.minimum)
        self.ok.setEnabled(valid)
        self.hint.setText(f"范围：{rect} 像素" if valid else f"请框选至少 {self.minimum} × {self.minimum} 像素的区域。")


class MinimapSettingsDialog(QDialog):
    """Settings entry point; screen capture and persistence belong to the app."""

    def __init__(self, status, position=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("小地图位置设置")
        self.setMinimumWidth(460)
        self.action = None
        layout = QVBoxLayout(self)
        summary = QLabel(status)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        if position:
            x, y, w, h = position.rect
            layout.addWidget(QLabel(f"游戏画面内的位置：({x}, {y})，范围 {w} × {h} 像素"))
        explanation = QLabel("此设置保存在本机，所有区域共用，无需先建立区域。\n"
                             "游戏窗口移动后可继续使用；分辨率、窗口大小或游戏 UI 布局改变后，请重新框选。\n"
                             "框选前请关闭游戏大地图，确保小地图完整可见。")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        select = QPushButton("重新框选小地图位置" if position else "从游戏画面框选小地图位置")
        select.clicked.connect(lambda: self.choose("select"))
        layout.addWidget(select)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose(self, action):
        self.action = action
        self.accept()


class CalibrationDialog(QDialog):
    def __init__(self, reference, query, parent=None):
        super().__init__(parent)
        self.setWindowTitle("辅助校准 · 选择同一组地标")
        self.resize(1220, 760)
        self.source, self.destination = [], []
        self.matrix = None
        layout = QVBoxLayout(self)
        self.hint = QLabel("先点左侧底图中的地标 1，再点右侧截图中的同一地标。至少三组，尽量分散。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        self.reference = ImageView(reference, "pick")
        self.query = ImageView(query, "pick")
        for title, view in [("保存的底图", self.reference), ("当前截图", self.query)]:
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
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
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.strokes = []
        self.matrix = None
        self.color = "#63f5c3"
        self.pixel_size = (1, 1)
        self.capture_box = None

    def display(self, box, strokes, query_to_ref, color):
        geometry, _ = logical_geometry(box)
        self.setGeometry(geometry)
        self.pixel_size = box[2:]
        self.capture_box = tuple(box)
        self.strokes, self.matrix, self.color = strokes, inverse(query_to_ref), color
        if not self.isVisible():
            self.show()
            if not native.exclude(int(self.winId())):
                self.hide()
                raise OSError("系统无法将叠加层排除出截屏，已停止显示，避免识别到路线自身。")
        self.update()

    def paintEvent(self, _):
        if self.matrix is None:
            return
        painter = QPainter(self)
        painter.scale(self.width() / self.pixel_size[0], self.height() / self.pixel_size[1])
        w, h = self.pixel_size
        clip = QPainterPath()
        radius = min(w, h) * .445
        clip.addEllipse(QPointF(w / 2, h / 2), radius, radius)
        painter.setClipPath(clip)
        paint_routes(painter, self.strokes, self.matrix, self.color)


class DrawingOverlay(QDialog):
    """Draw over a frozen map capture at its original desktop position."""

    def __init__(self, image, box, strokes, query_to_ref, color, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        geometry, _ = logical_geometry(box)
        self.setGeometry(geometry)
        self.background = qimage(image)
        self.pixel_size = (image.shape[1], image.shape[0])
        self.strokes = copy.deepcopy(strokes)
        self.query_to_ref = query_to_ref
        self.ref_to_query = inverse(query_to_ref)
        self.color = color
        self.current = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.current = [self.to_reference(event.position())]

    def to_reference(self, point):
        query_point = [point.x() * self.pixel_size[0] / self.width(),
                       point.y() * self.pixel_size[1] / self.height()]
        return transform_points([query_point], self.query_to_ref)[0].tolist()

    def mouseMoveEvent(self, event):
        if self.current is not None:
            point = self.to_reference(event.position())
            if np.linalg.norm(np.array(point) - self.current[-1]) > 1:
                self.current.append(point)
                self.update()

    def mouseReleaseEvent(self, event):
        if self.current is not None and event.button() == Qt.MouseButton.LeftButton:
            if len(self.current) >= 2:
                self.strokes.append(self.current)
            self.current = None
            self.update()

    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.background)
        painter.save()
        painter.scale(self.width() / self.pixel_size[0], self.height() / self.pixel_size[1])
        paint_routes(painter, self.strokes + ([self.current] if self.current else []), self.ref_to_query, self.color)
        painter.restore()
        painter.fillRect(0, 0, self.width(), 38, QColor(8, 17, 24, 225))
        painter.setPen(QColor("#e7f4f2"))
        painter.drawText(QRect(14, 0, self.width() - 28, 38), Qt.AlignmentFlag.AlignVCenter,
                         "冻结画面绘制 · Enter 确认 · Esc 取消 · Ctrl+Z 撤销（绘制时地图不会移动）")
