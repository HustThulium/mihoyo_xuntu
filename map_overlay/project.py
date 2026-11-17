from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import struct
import tempfile
import zipfile

import cv2
import numpy as np

def read_image(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法读取图片，请使用 PNG 或 JPG。")
    check_image(image)
    return image


def check_image(image):
    h, w = image.shape[:2]
    if min(h, w) < 64 or max(h, w) > 8192 or h * w > 30_000_000:
        raise ValueError("底图尺寸应在 64～8192 像素之间，总像素不超过 3000 万。")


@dataclass
class Project:
    image: np.ndarray
    name: str = "未命名区域"
    strokes: list = field(default_factory=list)
    target_title: str = ""
    color: str = "#63f5c3"

    def save(self, path):
        check_image(self.image)
        payload = dict(version=1, name=self.name, strokes=self.strokes,
                       target_title=self.target_title, color=self.color)
        encoded = cv2.imencode(".png", self.image)[1].tobytes()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as bundle:
                    bundle.writestr("project.json", json.dumps(payload, ensure_ascii=False))
                    bundle.writestr("reference.png", encoded)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path):
        with zipfile.ZipFile(path) as bundle:
            if sum(item.file_size for item in bundle.infolist()) > 128_000_000:
                raise ValueError("区域文件过大。")
            metadata = bundle.read("project.json")
            if len(metadata) > 12_000_000:
                raise ValueError("路线数据过大。")
            payload = json.loads(metadata)
            if payload.get("version") != 1:
                raise ValueError("不支持的区域文件版本。")
            png = bundle.read("reference.png")
        if png[:8] != b"\x89PNG\r\n\x1a\n" or len(png) < 24:
            raise ValueError("底图格式无效。")
        width, height = struct.unpack(">II", png[16:24])
        if not 64 <= min(width, height) or max(width, height) > 8192 or width * height > 30_000_000:
            raise ValueError("底图尺寸无效。")
        image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("底图损坏。")
        strokes = payload.get("strokes", [])
        if not isinstance(strokes, list) or sum(len(s) for s in strokes) > 100_000:
            raise ValueError("路线点数量无效。")
        normalized = []
        for stroke in strokes:
            arr = np.asarray(stroke, dtype=float)
            if arr.ndim != 2 or arr.shape[1] != 2 or not np.isfinite(arr).all() or np.abs(arr).max(initial=0) > 1_000_000:
                raise ValueError("路线坐标无效。")
            if len(arr) >= 2:
                normalized.append(arr.tolist())
        color = payload.get("color", "#63f5c3")
        if not isinstance(color, str) or len(color) != 7 or color[0] != "#":
            raise ValueError("路线颜色无效。")
        try:
            int(color[1:], 16)
        except ValueError as exc:
            raise ValueError("路线颜色无效。") from exc
        return cls(image, str(payload.get("name", "未命名区域"))[:100], normalized,
                   str(payload.get("target_title", ""))[:500], color)
