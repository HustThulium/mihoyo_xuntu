from __future__ import annotations

import threading
import time

import mss
import numpy as np
from PySide6.QtCore import QThread, Signal

from . import native
from .vision import Localizer, Match, PoseGate


class CapturePause:
    """Pause both workers and wait for any desktop read already in progress."""

    def __init__(self):
        self._lock = threading.Lock()
        self.revision = 0
        self.paused = False

    def set_paused(self, paused):
        with self._lock:
            if paused != self.paused:
                self.revision += 1
            self.paused = paused

    def capture(self, read, *args):
        with self._lock:
            return self.revision, None if self.paused else read(*args)


def grab(box, session=None):
    x, y, w, h = box
    region = dict(left=x, top=y, width=w, height=h)
    if session is None:
        with mss.mss() as screen:
            return np.asarray(screen.grab(region))[:, :, :3].copy()
    return np.asarray(session.grab(region))[:, :, :3].copy()


def capture_minimap(hwnd, position, session):
    """Capture pixels only, with the same desktop/focus constraints as tracking."""
    payload = dict(time=time.monotonic(), image=None, box=None)
    box = native.client_box(hwnd, strict=False)
    if box is None:
        return dict(payload, status="实时同步已暂停：游戏窗口已关闭或最小化")
    if not position.matches(box[2:]):
        return dict(payload, status="实时同步已暂停：窗口尺寸变化，请重新设置位置")
    if not native.foreground_is(hwnd):
        return dict(payload, status="实时同步已暂停：等待游戏回到前台")
    native.monitor_for(box)
    absolute = position.absolute_box(box)
    image = grab(absolute, session)
    if native.client_box(hwnd, strict=False) != box or not native.foreground_is(hwnd):
        return dict(payload, status="实时同步已暂停：窗口位置或前台状态变化")
    return dict(payload, image=image, box=absolute, status="实时同步 · 最多 3 帧/秒",
                capture_ms=(time.monotonic() - payload["time"]) * 1000)


class PreviewWorker(QThread):
    """Low-frequency raw preview; independent of reference maps and localization."""
    result = Signal(object)
    interval = 1 / 3

    def __init__(self, hwnd, position, generation, capture_pause=None):
        super().__init__()
        self.hwnd = hwnd
        self.position = position
        self.generation = generation
        self.capture_pause = capture_pause if capture_pause is not None else CapturePause()
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            with mss.mss() as screen:
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    revision, payload = self.capture_pause.capture(capture_minimap, self.hwnd, self.position, screen)
                    if payload is not None:
                        self.result.emit(dict(payload, generation=self.generation, capture_revision=revision))
                    self.stop_event.wait(max(.02, self.interval - (time.monotonic() - started)))
        except Exception as exc:
            self.result.emit(dict(generation=self.generation, time=time.monotonic(), image=None,
                                  box=None, status=f"实时同步失败：{exc}", fatal=True))


class TrackingWorker(QThread):
    result = Signal(object)

    def __init__(self, image, hwnd, position, generation, seed=None, capture_pause=None):
        super().__init__()
        self.image = image
        self.hwnd = hwnd
        self.position = position
        self.rect = position.rect
        self.client_size = position.client_size
        self.generation = generation
        self.seed = seed
        self.capture_pause = capture_pause if capture_pause is not None else CapturePause()
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            cv_localizer = Localizer(self.image)
            cv_localizer.reset(self.seed)
            gate = PoseGate()
            previous_revision = None
            with mss.mss() as screen:
                while not self.stop_event.is_set():
                    revision = self.capture_pause.revision
                    if self.capture_pause.paused:
                        self.stop_event.wait(.10)
                        continue
                    started = time.monotonic()
                    payload = dict(generation=self.generation, time=started, ready=False,
                                   match=Match(reason="等待目标游戏窗口成为前台"), image=None, box=None)
                    box = native.client_box(self.hwnd, strict=False)
                    if box is None:
                        payload["match"].reason = "目标窗口已关闭或最小化"
                        gate.reset()
                        cv_localizer.reset()
                    elif not self.position.matches(box[2:]):
                        payload["match"].reason = "游戏窗口尺寸已变化，请在“小地图位置设置”中重新框选"
                        gate.reset()
                        cv_localizer.reset()
                    elif native.foreground_is(self.hwnd):
                        native.monitor_for(box)
                        absolute = self.position.absolute_box(box)
                        revision, image = self.capture_pause.capture(grab, absolute, screen)
                        if image is None:
                            self.stop_event.wait(.10)
                            continue
                        if previous_revision is not None and revision != previous_revision:
                            gate.reset()
                            cv_localizer.reset()
                        previous_revision = revision
                        match = cv_localizer.locate(image)
                        ready = gate.accept(match, image.shape)
                        # Never emit an image captured across a window move or focus change.
                        if native.client_box(self.hwnd, strict=False) != box or not native.foreground_is(self.hwnd):
                            ready = False
                            gate.reset()
                            cv_localizer.reset()
                            match = Match(reason="窗口位置或前台状态变化，等待重新定位")
                        payload.update(match=match, ready=ready, image=image, box=absolute)
                    else:
                        gate.reset()
                        cv_localizer.reset()
                    self.result.emit(dict(payload, capture_revision=revision))
                    self.stop_event.wait(max(.02, .10 - (time.monotonic() - started)))
        except Exception as exc:
            self.result.emit(dict(generation=self.generation, time=time.monotonic(), ready=False,
                                  match=Match(reason=f"捕获或定位失败：{exc}"), image=None, box=None,
                                  fatal=True))
