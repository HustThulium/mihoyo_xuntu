from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import partial

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QTransform

from .project import INK_FORMAT


INK_WIDTH = 3
UNDO_BYTES = 64 * 1024**2
UNDO_STEPS = 100


@dataclass
class Stroke:
    path: QPainterPath
    color: str

    def add_point(self, point):
        offset = point - self.path.currentPosition()
        if QPointF.dotProduct(offset, offset) >= 1:
            self.path.lineTo(point)


def paint_stroke(painter, stroke):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for color, width in [(QColor(7, 21, 27, 200), INK_WIDTH + 3), (QColor(stroke.color), INK_WIDTH)]:
        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if stroke.path.isEmpty():
            painter.drawPoint(stroke.path.currentPosition())
        else:
            painter.drawPath(stroke.path)


def affine_transform(matrix):
    (a, b, x), (c, d, y) = matrix
    return QTransform(a, c, b, d, x, y)


def marker_rect(marker, center, units_per_pixel=1.0):
    image = marker.asset.image
    scale = marker.size * units_per_pixel / max(image.width(), image.height())
    w, h = image.width() * scale, image.height() * scale
    return QRectF(center.x() - w / 2, center.y() - h / 2, w, h)


def paint_annotations(painter, project, ref_to_view, visible, units_per_pixel=1.0,
                      strokes=(), markers=None):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if project.ink is not None or strokes:
        painter.save()
        painter.setWorldTransform(ref_to_view, combine=True)
        h, w = project.image.shape[:2]
        painter.setClipRect(QRectF(0, 0, w, h), Qt.ClipOperation.IntersectClip)
        if project.ink is not None:
            painter.drawImage(0, 0, project.ink)
        for stroke in strokes:
            paint_stroke(painter, stroke)
        painter.restore()
    for marker in project.markers if markers is None else markers:
        center = ref_to_view.map(QPointF(marker.x, marker.y))
        rect = marker_rect(marker, center, units_per_pixel)
        if visible.intersects(rect):
            painter.drawImage(rect, marker.asset.image)
    painter.restore()


def paint_minimap(painter, project, ref_to_view, frame, units_per_pixel=1.0):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    radius = min(frame.width(), frame.height()) * .445
    clip.addEllipse(frame.center(), radius, radius)
    painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    paint_annotations(painter, project, ref_to_view, clip.boundingRect(), units_per_pixel)
    painter.restore()


class AnnotationEdits:
    def __init__(self, project):
        self.project = project
        self.history = deque()
        self.history_bytes = 0

    def remember(self, action, cost=0):
        self.history.append((action, cost))
        self.history_bytes += cost
        while len(self.history) > UNDO_STEPS or (self.history_bytes > UNDO_BYTES and len(self.history) > 1):
            self.history_bytes -= self.history.popleft()[1]

    def undo(self):
        if not self.history:
            return False
        action, cost = self.history.pop()
        self.history_bytes -= cost
        action()
        return True

    def draw(self, stroke):
        h, w = self.project.image.shape[:2]
        margin = (INK_WIDTH + 3) / 2 + 1
        bounds = stroke.path.boundingRect().adjusted(-margin, -margin, margin, margin)
        rect = bounds.toAlignedRect().intersected(QRect(0, 0, w, h))
        if rect.isEmpty():
            return False
        before = None
        if self.project.ink is None:
            self.project.ink = QImage(w, h, INK_FORMAT)
            self.project.ink.fill(Qt.GlobalColor.transparent)
        else:
            before = self.project.ink.copy(rect)
        with QPainter(self.project.ink) as painter:
            painter.setClipRect(rect)
            paint_stroke(painter, stroke)
        self.remember(partial(self.restore_ink, rect, before), 0 if before is None else before.sizeInBytes())
        return True

    def restore_ink(self, rect, before):
        if before is None:
            self.project.ink = None
        else:
            with QPainter(self.project.ink) as painter:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.drawImage(rect.topLeft(), before)

    def add_marker(self, marker):
        index = len(self.project.markers)
        self.project.add_marker(marker)
        self.remember(partial(self.project.markers.pop, index))

    def update_marker(self, index, marker):
        markers = self.project.markers
        before = markers[index]
        if before == marker:
            return False
        markers[index] = marker
        self.remember(partial(markers.__setitem__, index, before))
        return True

    def remove_marker(self, index):
        markers = self.project.markers
        before = markers.pop(index)
        cost = len(before.asset.png) + before.asset.image.sizeInBytes()
        self.remember(partial(markers.insert, index, before), cost)
