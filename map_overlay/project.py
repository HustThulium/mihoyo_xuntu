from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import zipfile

import cv2
import numpy as np
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage


DEFAULT_COLOR = "#63f5c3"
DEFAULT_MARKER_SIZE = 28
MIN_MARKER_SIZE = 8
MAX_MARKER_SIZE = 96
MAX_MARKERS = 2000
MAX_PACK_BYTES = 256 * 1024**2
MAX_METADATA_BYTES = 1024**2
MAX_ASSET_BYTES = 4 * 1024**2
MAX_ASSET_DIMENSION = 1024
MAX_TOTAL_ASSET_BYTES = 24 * 1024**2
MAX_TOTAL_ASSET_PIXELS = 8_000_000
PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
INK_FORMAT = QImage.Format.Format_ARGB32_Premultiplied


def read_image(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法读取图片，请使用 PNG 或 JPG。")
    check_image(image)
    return image


def check_reference_size(width, height):
    if min(width, height) < 64 or max(width, height) > 8192 or width * height > 30_000_000:
        raise ValueError("底图尺寸应在 64～8192 像素之间，总像素不超过 3000 万。")


def check_image(image):
    check_reference_size(image.shape[1], image.shape[0])


def png_size(data, label):
    if len(data) < 33 or not data.startswith(PNG_HEADER):
        raise ValueError(f"{label}格式无效，请使用 PNG。")
    return struct.unpack(">II", data[16:24])


def decode_png(data, label):
    image = QImage.fromData(data, "PNG")
    if image.isNull():
        raise ValueError(f"{label}损坏。")
    return image.convertToFormat(INK_FORMAT)


def encode_ink(image):
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise ValueError("无法编码墨迹层。")
    return bytes(buffer.data())


@dataclass(frozen=True, eq=False)
class MarkerAsset:
    key: str
    png: bytes
    image: QImage


@dataclass(frozen=True)
class Marker:
    asset: MarkerAsset
    x: float
    y: float
    size: int = DEFAULT_MARKER_SIZE


def decode_asset(data, total_pixels=0):
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError("单张标记图片不能超过 4 MiB。")
    width, height = png_size(data, "标记图片")
    if not 1 <= min(width, height) or max(width, height) > MAX_ASSET_DIMENSION:
        raise ValueError(f"标记图片宽高应在 1～{MAX_ASSET_DIMENSION} 像素之间。")
    if total_pixels + width * height > MAX_TOTAL_ASSET_PIXELS:
        raise ValueError("标记图片总像素数过多。")
    return MarkerAsset(sha256(data).hexdigest(), data, decode_png(data, "标记图片"))


def read_marker_asset(path):
    with Path(path).open("rb") as file:
        return decode_asset(file.read(MAX_ASSET_BYTES + 1))


@dataclass
class Project:
    image: np.ndarray
    name: str = "未命名区域"
    target_title: str = ""
    color: str = DEFAULT_COLOR
    ink: QImage | None = None
    markers: list[Marker] = field(default_factory=list)

    @property
    def has_annotations(self):
        return self.ink is not None or bool(self.markers)

    def marker_assets(self):
        return {marker.asset.key: marker.asset for marker in self.markers}

    def add_marker(self, marker):
        if len(self.markers) >= MAX_MARKERS:
            raise ValueError(f"每个区域最多放置 {MAX_MARKERS} 个标记。")
        assets = self.marker_assets()
        if marker.asset.key in assets:
            marker = Marker(assets[marker.asset.key], marker.x, marker.y, marker.size)
        else:
            images = [*assets.values(), marker.asset]
            if (sum(len(asset.png) for asset in images) > MAX_TOTAL_ASSET_BYTES or
                    sum(asset.image.width() * asset.image.height() for asset in images) > MAX_TOTAL_ASSET_PIXELS):
                raise ValueError("标记图片总大小超出区域上限。")
        self.markers.append(marker)

    def save(self, path):
        check_image(self.image)
        payload = dict(version=2, name=self.name, target_title=self.target_title, color=self.color,
                       markers=[dict(asset=m.asset.key, x=m.x, y=m.y, size=m.size) for m in self.markers])
        encoded_ok, reference = cv2.imencode(".png", self.image)
        if not encoded_ok:
            raise ValueError("无法编码底图。")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                with zipfile.ZipFile(file, "w", zipfile.ZIP_STORED) as bundle:
                    bundle.writestr("project.json", json.dumps(payload, ensure_ascii=False, allow_nan=False))
                    bundle.writestr("reference.png", reference.tobytes())
                    if self.ink is not None:
                        bundle.writestr("ink.png", encode_ink(self.ink))
                    for key, asset in self.marker_assets().items():
                        bundle.writestr(f"assets/{key}.png", asset.png)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path):
        with zipfile.ZipFile(path) as bundle:
            members = {item.filename: item for item in bundle.infolist()}
            if len(members) != len(bundle.infolist()):
                raise ValueError("区域文件含有重名条目。")
            if sum(item.file_size for item in members.values()) > MAX_PACK_BYTES:
                raise ValueError("区域文件过大。")
            if "project.json" not in members or "reference.png" not in members:
                raise ValueError("区域文件缺少工程信息或底图。")
            if members["project.json"].file_size > MAX_METADATA_BYTES:
                raise ValueError("工程信息过大。")
            payload = json.loads(bundle.read("project.json"))
            if not isinstance(payload, dict) or payload.get("version") != 2:
                raise ValueError("不支持的区域文件版本。请建立新的区域。")
            color = payload.get("color", DEFAULT_COLOR)
            if not isinstance(color, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None:
                raise ValueError("画笔颜色无效。")
            png = bundle.read("reference.png")
            width, height = png_size(png, "底图")
            check_reference_size(width, height)
            image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("底图损坏。")
            del png
            ink = None
            if "ink.png" in members:
                png = bundle.read("ink.png")
                if png_size(png, "墨迹层") != (width, height):
                    raise ValueError("墨迹层尺寸与底图不同。")
                ink = decode_png(png, "墨迹层")
                del png
            markers = load_markers(bundle, members, payload.get("markers", []), width, height)
        return cls(image, str(payload.get("name", "未命名区域"))[:100],
                   str(payload.get("target_title", ""))[:500], color, ink, markers)


def load_markers(bundle, members, records, width, height):
    if not isinstance(records, list) or len(records) > MAX_MARKERS:
        raise ValueError("标记列表或数量无效。")
    assets = {}
    markers = []
    total_bytes = total_pixels = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("标记数据无效。")
        key, x, y, size = (record.get(field) for field in ("asset", "x", "y", "size"))
        if (type(x) not in (int, float) or type(y) not in (int, float) or
                not 0 <= x < width or not 0 <= y < height):
            raise ValueError("标记坐标超出底图。")
        if type(size) is not int or not MIN_MARKER_SIZE <= size <= MAX_MARKER_SIZE:
            raise ValueError("标记大小无效。")
        if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise ValueError("标记图片引用无效。")
        if key not in assets:
            name = f"assets/{key}.png"
            if name not in members:
                raise ValueError("区域文件缺少标记图片。")
            length = members[name].file_size
            if length > MAX_ASSET_BYTES or total_bytes + length > MAX_TOTAL_ASSET_BYTES:
                raise ValueError("区域文件中的标记图片过大。")
            asset = decode_asset(bundle.read(name), total_pixels)
            if asset.key != key:
                raise ValueError("标记图片与工程记录不一致。")
            assets[key] = asset
            total_bytes += length
            total_pixels += asset.image.width() * asset.image.height()
        markers.append(Marker(assets[key], x, y, size))
    return markers
