"""Local program and game-display settings, independent of map projects."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


DEFAULT_INK_OPACITY = 70


@dataclass(frozen=True)
class MinimapPosition:
    rect: tuple[int, int, int, int]
    client_size: tuple[int, int]

    def __post_init__(self):
        rect, size = self.rect, self.client_size
        if (not isinstance(rect, (list, tuple)) or len(rect) != 4 or
                any(type(v) is not int for v in rect) or
                min(rect[:2]) < 0 or min(rect[2:]) < 64):
            raise ValueError("小地图范围无效。")
        if (not isinstance(size, (list, tuple)) or len(size) != 2 or
                any(type(v) is not int or not 64 <= v <= 16384 for v in size) or
                rect[0] + rect[2] > size[0] or rect[1] + rect[3] > size[1]):
            raise ValueError("小地图范围超出游戏窗口。")
        object.__setattr__(self, "rect", tuple(rect))
        object.__setattr__(self, "client_size", tuple(size))

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("小地图位置配置无效。")
        return cls(payload.get("rect"), payload.get("client_size"))

    def to_payload(self):
        return dict(rect=list(self.rect), client_size=list(self.client_size))

    def matches(self, client_size):
        return self.client_size == tuple(client_size)

    def absolute_box(self, client_box):
        if not self.matches(client_box[2:]):
            raise ValueError("游戏窗口尺寸与小地图位置配置不符，请在“小地图位置设置”中重新框选。")
        x, y, w, h = self.rect
        return client_box[0] + x, client_box[1] + y, w, h


class MinimapSettings:
    def __init__(self, path):
        self.path = Path(path)
        self.position = None
        self.preview_enabled = True
        self.minimize_on_tracking = True
        self.ink_opacity = DEFAULT_INK_OPACITY
        self.load_error = None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("不支持的小地图位置配置版本。")
            enabled = payload.get("preview_enabled", True)
            if type(enabled) is not bool:
                raise ValueError("实时预览开关无效。")
            minimize = payload.get("minimize_on_tracking", True)
            if type(minimize) is not bool:
                raise ValueError("开始叠加后自动最小化开关无效。")
            position = payload.get("minimap")
            self.position = MinimapPosition.from_payload(position) if position is not None else None
            self.preview_enabled = enabled
            self.minimize_on_tracking = minimize
            opacity = payload.get("ink_opacity")
            if "ink_opacity" not in payload:
                transparency = payload.get("ink_transparency")
                # Older versions also wrote their default when saving unrelated settings.
                if type(transparency) is int and 0 <= transparency <= 100 and transparency != 60:
                    opacity = 100 - transparency
            if type(opacity) is int and 0 <= opacity <= 100:
                self.ink_opacity = opacity
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            self.load_error = f"小地图位置配置无法读取，请重新设置：{exc}"

    def save_position(self, position):
        if not isinstance(position, MinimapPosition):
            raise ValueError("小地图位置配置无效。")
        self._save(position, self.preview_enabled, self.minimize_on_tracking)

    def save_preview_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("实时预览开关无效。")
        self._save(self.position, enabled, self.minimize_on_tracking)

    def save_minimize_on_tracking(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("开始叠加后自动最小化开关无效。")
        self._save(self.position, self.preview_enabled, enabled)

    def save_ink_opacity(self, value):
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("墨迹不透明度应在 0% 到 100% 之间。")
        self._save(self.position, self.preview_enabled, self.minimize_on_tracking, value)

    def _save(self, position, preview_enabled, minimize_on_tracking, ink_opacity=None):
        if ink_opacity is None:
            ink_opacity = self.ink_opacity
        payload = dict(version=1, minimap=position.to_payload() if position else None,
                       preview_enabled=preview_enabled, minimize_on_tracking=minimize_on_tracking,
                       ink_opacity=ink_opacity)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        # Publish only after the persistent update succeeds.
        self.position = position
        self.preview_enabled = preview_enabled
        self.minimize_on_tracking = minimize_on_tracking
        self.ink_opacity = ink_opacity
        self.load_error = None

    def require_position(self, client_size):
        if self.load_error:
            raise ValueError(self.load_error)
        if self.position is None:
            raise ValueError("尚未设置小地图位置，请先打开“小地图位置设置”并框选一次。")
        if not self.position.matches(client_size):
            raise ValueError("游戏窗口尺寸与小地图位置配置不符，请在“小地图位置设置”中重新框选。")
        return self.position

    def status(self, client_size=None):
        if self.load_error:
            return self.load_error
        if self.position is None:
            return "首次使用：请先设置小地图位置。"
        width, height = self.position.client_size
        if client_size is not None and not self.position.matches(client_size):
            return f"已保存 {width} × {height} 配置；当前窗口尺寸不同，需要重新设置。"
        return f"已保存小地图位置 · 适用画面 {width} × {height}"
