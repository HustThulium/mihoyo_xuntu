from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import partial

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QTransform

from .project import INK_FORMAT


INK_WIDTH = 3
INK_OUTLINE = 3
ERASER_DIAMETER = 32
MAX_ERASER_DIAMETER = 512
UNDO_BYTES = 64 * 1024**2
UNDO_STEPS = 100


@dataclass
class Stroke:
    path: QPainterPath
    color: str
    width: int = INK_WIDTH

    def add_point(self, point):
        offset = point - self.path.currentPosition()
        if QPointF.dotProduct(offset, offset) >= 1:
            self.path.lineTo(point)


def paint_ink_path(painter, path, color, width):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if path.isEmpty():
        painter.drawPoint(path.currentPosition())
    else:
        painter.drawPath(path)


def paint_stroke(painter, stroke):
    paint_ink_path(painter, stroke.path, QColor(7, 21, 27, 200), stroke.width + INK_OUTLINE)
    paint_ink_path(painter, stroke.path, QColor(stroke.color), stroke.width)


def erase_ink(ink, path, diameter):
    with QPainter(ink) as painter:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        paint_ink_path(painter, path, QColor(Qt.GlobalColor.black), diameter)


def ink_path_rect(path, width, bounds):
    margin = width / 2 + 1
    return path.boundingRect().adjusted(-margin, -margin, margin, margin).toAlignedRect().intersected(bounds)


def affine_transform(matrix):
    (a, b, x), (c, d, y) = matrix
    return QTransform(a, c, b, d, x, y)


def marker_rect(marker, center, units_per_pixel=1.0):
    image = marker.asset.image
    scale = marker.size * units_per_pixel / max(image.width(), image.height())
    w, h = image.width() * scale, image.height() * scale
    return QRectF(center.x() - w / 2, center.y() - h / 2, w, h)


def paint_annotations(painter, project, ref_to_view, visible, units_per_pixel=1.0,
                      strokes=(), markers=None, ink_opacity=1.0, *, ink_preview=None):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    ink = project.ink if ink_preview is None else ink_preview
    if ink is not None or strokes:
        painter.save()
        painter.setOpacity(painter.opacity() * ink_opacity)
        painter.setWorldTransform(ref_to_view, combine=True)
        h, w = project.image.shape[:2]
        painter.setClipRect(QRectF(0, 0, w, h), Qt.ClipOperation.IntersectClip)
        if ink is not None:
            painter.drawImage(0, 0, ink)
        for stroke in strokes:
            paint_stroke(painter, stroke)
        painter.restore()
    paint_markers(painter, project.markers if markers is None else markers, ref_to_view, visible, units_per_pixel)
    painter.restore()


def paint_markers(painter, markers, ref_to_view, visible, units_per_pixel):
    for marker in markers:
        center = ref_to_view.map(QPointF(marker.x, marker.y))
        rect = marker_rect(marker, center, units_per_pixel)
        if visible.intersects(rect):
            painter.drawImage(rect, marker.asset.image)


def paint_minimap(painter, project, ref_to_view, frame, units_per_pixel=1.0, ink_opacity=1.0):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    radius = min(frame.width(), frame.height()) * .445
    clip.addEllipse(frame.center(), radius, radius)
    painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    paint_annotations(painter, project, ref_to_view, clip.boundingRect(), units_per_pixel, ink_opacity=ink_opacity)
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
        rect = ink_path_rect(stroke.path, stroke.width + INK_OUTLINE, QRect(0, 0, w, h))
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

    def commit_erasure(self, preview, path, diameter):
        original = self.project.ink
        if original == preview:
            return False
        rect = ink_path_rect(path, diameter, original.rect())
        before = original.copy(rect)
        # Fully transparent pixels are zero in the premultiplied ink format.
        self.project.ink = preview if np.frombuffer(preview.constBits(), np.uint32).any() else None
        self.remember(partial(self.restore_ink, rect, before), before.sizeInBytes())
        return True

    def restore_ink(self, rect, before):
        if before is None:
            self.project.ink = None
        else:
            if self.project.ink is None:
                h, w = self.project.image.shape[:2]
                self.project.ink = QImage(w, h, INK_FORMAT)
                self.project.ink.fill(Qt.GlobalColor.transparent)
            with QPainter(self.project.ink) as painter:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.drawImage(rect.topLeft(), before)

    def merge_ink(self, origin, image):
        h, w = self.project.image.shape[:2]
        x, y = origin
        rect = QRect(x, y, image.width(), image.height())
        if not QRect(0, 0, w, h).contains(rect):
            raise ValueError("导入墨迹超出了底图范围。")
        before = None
        if self.project.ink is None:
            self.project.ink = QImage(w, h, INK_FORMAT)
            self.project.ink.fill(Qt.GlobalColor.transparent)
        else:
            before = self.project.ink.copy(rect)
        with QPainter(self.project.ink) as painter:
            painter.drawImage(x, y, image)
        self.remember(partial(self.restore_ink, rect, before), before.sizeInBytes() if before else 0)

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
