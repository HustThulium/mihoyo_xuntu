"""Deterministic synthetic terrain for offline validation; no game assets."""
import cv2
import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

from .annotations import AnnotationEdits, Stroke
from .geometry import inverse
from .project import Marker, Project, decode_asset


def make_demo():
    rng = np.random.default_rng(7821)
    image = np.full((800, 1100, 3), (82, 110, 94), np.uint8)
    noise = rng.normal(0, 3, image.shape[:2]).astype(np.int16)
    image = np.clip(image.astype(np.int16) + noise[..., None], 0, 255).astype(np.uint8)
    river = np.array([[120, 0], [210, 100], [140, 250], [280, 390], [220, 570], [350, 800]], np.int32)
    cv2.polylines(image, [river], False, (133, 116, 65), 95, cv2.LINE_AA)
    cv2.polylines(image, [river], False, (159, 137, 68), 76, cv2.LINE_AA)
    for _ in range(260):
        x, y = rng.integers([340, 30], [1050, 760])
        w, h = rng.integers(5, 28, 2)
        shade = int(rng.integers(110, 185))
        cv2.rectangle(image, (int(x), int(y)), (int(x+w), int(y+h)), (shade-20, shade, shade+8), -1)
        cv2.rectangle(image, (int(x), int(y)), (int(x+w), int(y+h)), (59, 73, 61), 1)
    for _ in range(90):
        x, y = map(int, rng.integers([20, 20], [1080, 780]))
        cv2.circle(image, (x, y), int(rng.integers(2, 11)), (65, 94, 74), -1, cv2.LINE_AA)
    roads = [np.array([[360, 800], [440, 650], [520, 500], [680, 430], [850, 280], [1050, 170]]),
             np.array([[0, 450], [300, 430], [520, 500], [720, 600], [1100, 650]]),
             np.array([[470, 0], [460, 200], [550, 330], [520, 500]])]
    for road in roads:
        cv2.polylines(image, [road.astype(np.int32)], False, (67, 75, 72), 15, cv2.LINE_AA)
        cv2.polylines(image, [road.astype(np.int32)], False, (164, 180, 190), 9, cv2.LINE_AA)
    cv2.circle(image, (520, 500), 32, (60, 77, 78), -1, cv2.LINE_AA)
    cv2.circle(image, (520, 500), 24, (134, 172, 184), -1, cv2.LINE_AA)
    cv2.circle(image, (520, 500), 12, (91, 127, 134), -1, cv2.LINE_AA)
    project = Project(image, "演示区域（合成地图）")
    edits = AnnotationEdits(project)
    for color, points in [
        (project.color, [(440, 650), (480, 570), (520, 500), (600, 465), (680, 430), (760, 360)]),
        ("#ffce73", [(520, 500), (585, 528), (643, 556), (720, 600)]),
    ]:
        path = QPainterPath(QPointF(*points[0]))
        for x, y in points[1:]:
            path.lineTo(x, y)
        edits.draw(Stroke(path, color))
    icon = np.zeros((36, 36, 4), np.uint8)
    cv2.circle(icon, (18, 18), 16, (48, 33, 23, 255), -1, cv2.LINE_AA)
    cv2.fillConvexPoly(icon, np.array([(18, 7), (29, 18), (18, 29), (7, 18)]),
                       (115, 206, 255, 255), cv2.LINE_AA)
    asset = decode_asset(cv2.imencode(".png", icon)[1].tobytes())
    project.add_marker(Marker(asset, 573, 439))
    project.add_marker(Marker(asset, 643, 556, 22))
    return project


def mini_at(reference, center=(560, 485), scale=.85, angle=0, size=280, hud=True):
    theta = np.radians(angle)
    a, b = scale*np.cos(theta), scale*np.sin(theta)
    matrix = np.array([[a, -b, center[0] - (a-b)*size/2],
                       [b, a, center[1] - (a+b)*size/2]])
    image = cv2.warpAffine(reference, inverse(matrix), (size, size))
    if hud:
        yy, xx = np.mgrid[:size, :size]
        outside = (xx-size/2)**2 + (yy-size/2)**2 > (size*.47)**2
        image[outside] = (24, 25, 33)
        cv2.circle(image, (size//2, size//2), round(size*.47), (180, 180, 175), 3, cv2.LINE_AA)
        c = size//2
        cv2.fillConvexPoly(image, np.array([[c, c-12], [c-9, c+8], [c, c+3], [c+9, c+8]], np.int32), (255, 236, 20))
        cv2.circle(image, (c+55, c-40), 9, (250, 250, 250), -1)
    return image, matrix
