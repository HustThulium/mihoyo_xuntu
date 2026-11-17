from __future__ import annotations

import cv2
import numpy as np


def transform_points(points, matrix):
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if not len(points):
        return points.copy()
    return points @ np.asarray(matrix)[:, :2].T + np.asarray(matrix)[:, 2]


def inverse(matrix):
    return cv2.invertAffineTransform(np.asarray(matrix, dtype=np.float64))


def similarity(source, destination):
    """Least-squares similarity fit, with diagnostics in destination pixels.

    Used for user-picked landmarks, not to manufacture visual tracking confidence.
    """
    src = np.asarray(source, dtype=float).reshape(-1, 2)
    dst = np.asarray(destination, dtype=float).reshape(-1, 2)
    if len(src) < 3 or len(src) != len(dst):
        raise ValueError("请至少选择三组对应地标。")
    if not np.isfinite(src).all() or not np.isfinite(dst).all():
        raise ValueError("地标坐标无效。")
    if np.linalg.svd(src - src.mean(axis=0), compute_uv=False)[-1] < 2:
        raise ValueError("地标过于集中或接近同一直线，请选择分散的地标。")
    design = np.zeros((len(src) * 2, 4))
    design[0::2] = np.c_[src[:, 0], -src[:, 1], np.ones(len(src)), np.zeros(len(src))]
    design[1::2] = np.c_[src[:, 1], src[:, 0], np.zeros(len(src)), np.ones(len(src))]
    a, b, tx, ty = np.linalg.lstsq(design, dst.reshape(-1), rcond=None)[0]
    mat = np.array([[a, -b, tx], [b, a, ty]])
    if not 0.02 < np.hypot(a, b) < 50:
        raise ValueError("计算出的比例异常，请检查对应地标。")
    errors = np.linalg.norm(transform_points(src, mat) - dst, axis=1)
    return mat, float(np.sqrt(np.mean(errors**2)))


def minimap_mask(image):
    h, w = image.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    radius = int(min(w, h) * 0.445)
    cv2.circle(mask, (w // 2, h // 2), radius, 255, -1)
    cv2.circle(mask, (w // 2, h // 2), max(10, int(radius * 0.17)), 0, -1)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Bright white UI and saturated cyan player/teleport icons are unreliable.
    icons = cv2.inRange(hsv, (0, 0, 232), (179, 45, 255))
    icons |= cv2.inRange(hsv, (80, 150, 215), (105, 255, 255))
    icons = cv2.dilate(icons, np.ones((5, 5), np.uint8))
    mask[icons > 0] = 0
    return mask
