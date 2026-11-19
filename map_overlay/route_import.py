from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtGui import QImage

from .geometry import transform_points
from .project import INK_FORMAT


class RouteExtraction:
    """Aligned source colours and a reusable difference map in reference pixels."""

    def __init__(self, reference, source, matrix, clip=None):
        h, w = source.shape[:2]
        rh, rw = reference.shape[:2]
        outline = [(0, 0), (w, 0), (0, h), (w, h)] if clip is None else clip
        corners = transform_points(outline, matrix)
        x, y = np.maximum(np.floor(corners.min(axis=0)).astype(int), 0)
        right, bottom = np.minimum(np.ceil(corners.max(axis=0)).astype(int) + 1, [rw, rh])
        if right <= x or bottom <= y:
            raise ValueError("所选地图与当前区块没有重叠。")
        self.origin = int(x), int(y)
        self.background = reference[y:bottom, x:right]
        size = int(right-x), int(bottom-y)
        local = matrix.copy()
        local[:, 2] -= self.origin
        self.colors = cv2.warpAffine(source, local, size)
        if clip is None:
            source_mask = np.full((h, w), 255, np.uint8)
        else:
            source_mask = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(source_mask, np.rint(clip * 16).astype(np.int32), 255, shift=4)
        coverage = cv2.warpAffine(source_mask, local, size)
        self.score = self.difference()
        self.score[coverage < 255] = 0

    def difference(self):
        # Estimate slowly varying brightness changes on a coarse grid. A bounded
        # correction and median filter keep narrow annotations out of the estimate.
        h, w = self.colors.shape[:2]
        grid = max(1, w // 12), max(1, h // 12)
        def brightness(image):
            small = cv2.resize(image, grid, interpolation=cv2.INTER_AREA)
            return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)

        bias = np.clip(brightness(self.colors) - brightness(self.background), -48, 48)
        bias = cv2.medianBlur((bias + 128).astype(np.uint8), 7)
        bias = cv2.resize(bias, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.int16) - 128
        score = np.zeros((h, w), np.uint8)
        for channel in range(3):
            expected = self.background[:, :, channel].astype(np.int16) + bias
            # A one-pixel neighbourhood tolerates resampling along terrain edges.
            expected = np.uint8(np.clip(expected, 0, 255))
            source = self.colors[:, :, channel]
            excess = cv2.subtract(source, cv2.dilate(expected, None))
            deficit = cv2.subtract(cv2.erode(expected, None), source)
            np.maximum(score, cv2.max(excess, deficit), out=score)
        return score

    def image(self, sensitivity, clean=False):
        threshold = round(100 - .96 * sensitivity)
        # Smooth the cut-off over eight intensity levels to avoid jagged edges.
        lut = np.clip((np.arange(256) - threshold) * 32, 0, 255).astype(np.uint8)
        alpha = cv2.LUT(self.score, lut)
        if clean:
            density = cv2.boxFilter((alpha > 0).astype(np.uint8), cv2.CV_16U,
                                    (7, 7), normalize=False, borderType=cv2.BORDER_CONSTANT)
            weak = cv2.dilate(self.score, None) < threshold + 22
            alpha[(density <= 5) & weak] = 0
        rgba = cv2.cvtColor(self.colors, cv2.COLOR_BGR2RGBA)
        rgba[:, :, 3] = alpha
        h, w = alpha.shape
        return QImage(rgba.data, w, h, rgba.strides[0], QImage.Format.Format_RGBA8888).convertToFormat(INK_FORMAT)
