from __future__ import annotations

from dataclasses import dataclass
import time
import cv2
import numpy as np

from .geometry import inverse, minimap_mask, transform_points


@dataclass
class Match:
    matrix: np.ndarray | None = None  # query pixels -> reference pixels
    inliers: int = 0
    candidates: int = 0
    error: float = float("inf")  # query pixels
    coverage: float = 0
    elapsed_ms: float = 0
    reason: str = "未定位"
    scope: str = "区域"

    @property
    def ok(self):
        return self.matrix is not None


def gray(image):
    # Local histogram tiles change with map zoom/crop and can alter descriptors.
    # SIFT already normalizes local contrast; retain the original terrain edges.
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


class Localizer:
    """Independent SIFT + FLANN + robust similarity registration.

    No external game assets or BetterGI source are bundled. All coordinates refer
    to the user-provided reference image. A previous pose only narrows search;
    every accepted pose must be supported by current-frame visual matches.
    """

    def __init__(self, reference):
        self.shape = reference.shape[:2]
        self.detector = cv2.SIFT_create(nfeatures=16000, contrastThreshold=0.018,
                                       edgeThreshold=14)
        self.ref_scale = min(1.0, 3000 / max(self.shape))
        work = cv2.resize(reference, None, fx=self.ref_scale, fy=self.ref_scale) if self.ref_scale < 1 else reference
        keypoints, self.descriptors = self.detector.detectAndCompute(gray(work), None)
        self.points = np.array([p.pt for p in keypoints], np.float32).reshape(-1, 2) / self.ref_scale
        self.full_matcher = self._make_matcher(self.descriptors)
        self.previous = None
        self.failures = 0
        self.frames = 0

    @staticmethod
    def _make_matcher(descriptors):
        if descriptors is None or len(descriptors) < 2:
            return None
        matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=80))
        matcher.add([descriptors])
        matcher.train()
        return matcher

    def reset(self, seed=None):
        self.previous = seed
        self.failures = 0

    def locate(self, query):
        start = time.perf_counter()
        self.frames += 1
        scale = min(1.0, 900 / max(query.shape[:2]))
        work = cv2.resize(query, None, fx=scale, fy=scale) if scale < 1 else query
        mask = minimap_mask(work)
        keypoints, descriptors = self.detector.detectAndCompute(gray(work), mask)
        points = np.array([p.pt for p in keypoints], np.float32).reshape(-1, 2) / scale
        result = Match(reason="地形特征不足，请换清晰底图或辅助校准")
        if descriptors is not None and len(points) >= 8 and self.full_matcher is not None:
            # Reacquisition periodically uses the whole saved region as a guard
            # against locking onto a repeated nearby texture indefinitely.
            if self.previous is not None and self.failures < 3 and self.frames % 30:
                h, w = query.shape[:2]
                center = transform_points([[w / 2, h / 2]], self.previous)[0]
                factor = np.linalg.norm(self.previous[:, 0])
                radius = factor * max(w, h) * (0.85 + self.failures * 0.4)
                chosen = np.where((np.abs(self.points - center) < radius).all(axis=1))[0]
                if len(chosen) >= 12:
                    matcher = self._make_matcher(self.descriptors[chosen])
                    result = self._match(points, descriptors, self.points[chosen], matcher, query.shape)
                    result.scope = "附近"
            if not result.ok:
                result = self._match(points, descriptors, self.points, self.full_matcher, query.shape)
            if result.ok:
                self.previous = result.matrix.copy()
                self.failures = 0
            else:
                self.failures += 1
                if self.failures >= 3:
                    self.previous = None
        result.elapsed_ms = (time.perf_counter() - start) * 1000
        return result

    def _match(self, query_points, descriptors, ref_points, matcher, shape):
        if matcher is None:
            return Match(reason="底图特征不足")
        pairs = matcher.knnMatch(descriptors, k=2)
        good = [a for pair in pairs if len(pair) == 2
                for a, b in [pair] if a.distance < 0.77 * b.distance]
        # Multiple query features may otherwise vote for the same map feature.
        unique = {}
        for match in good:
            if match.trainIdx not in unique or match.distance < unique[match.trainIdx].distance:
                unique[match.trainIdx] = match
        good = list(unique.values())
        if len(good) < 8:
            return Match(candidates=len(good), reason="对应特征不足，等待重新定位")
        src = query_points[[m.queryIdx for m in good]]
        dst = ref_points[[m.trainIdx for m in good]]
        # Fit in query space so the RANSAC threshold is independent of map zoom.
        ref_to_query, flags = cv2.estimateAffinePartial2D(
            dst, src, method=cv2.RANSAC, ransacReprojThreshold=3.0,
            maxIters=3500, confidence=0.995, refineIters=15)
        if ref_to_query is None or flags is None:
            return Match(candidates=len(good), reason="对应地形不一致")
        accepted = flags.ravel().astype(bool)
        count = int(accepted.sum())
        result = Match(inliers=count, candidates=len(good))
        if count < 8 or count / len(good) < 0.42:
            result.reason = "匹配可信度不足，已隐藏路线"
            return result
        h, w = shape[:2]
        hull_area = cv2.contourArea(cv2.convexHull(src[accepted]))
        coverage = hull_area / (w * h)
        span = np.ptp(src[accepted], axis=0) / [w, h]
        errors = np.linalg.norm(transform_points(dst[accepted], ref_to_query) - src[accepted], axis=1)
        error = float(np.sqrt(np.mean(errors**2)))
        matrix = inverse(ref_to_query)
        ratio = np.linalg.norm(matrix[:, 0])
        angle = np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0]))
        center = transform_points([[w / 2, h / 2]], matrix)[0]
        result.error, result.coverage = error, coverage
        if coverage < 0.025 or min(span) < 0.18:
            result.reason = "地标过于集中，已隐藏路线"
        elif error > 2.5 or not 0.04 < ratio < 30:
            result.reason = "比例或误差异常，已隐藏路线"
        elif abs(angle) > 8:
            result.reason = "仅支持固定朝北地图，请检查方向设置"
        elif not (0 <= center[0] < self.shape[1] and 0 <= center[1] < self.shape[0]):
            result.reason = "当前位置超出已保存区域"
        else:
            result.matrix = matrix
            result.reason = "已定位"
        return result


class PoseGate:
    """Require two consistent frames after loss, a jump or a scale change."""

    def __init__(self):
        self.pending = None
        self.last = None

    def reset(self):
        self.pending = self.last = None

    def accept(self, match, shape):
        if not match.ok:
            self.reset()
            return False
        h, w = shape[:2]
        probe = [[w / 2, h / 2], [w / 2 + w * .3, h / 2]]
        current = transform_points(probe, match.matrix)
        scale = np.linalg.norm(match.matrix[:, 0])

        def near(other):
            if other is None:
                return False
            old_scale = np.linalg.norm(other[:, 0])
            return (.88 < scale / old_scale < 1.14 and
                    np.max(np.linalg.norm(current - transform_points(probe, other), axis=1)) / scale < w * .22)

        if near(self.last):
            self.last = match.matrix.copy()
            return True
        self.last = None
        if near(self.pending):
            self.last = match.matrix.copy()
            self.pending = None
            return True
        self.pending = match.matrix.copy()
        return False
