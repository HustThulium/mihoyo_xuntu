"""Live OS capture/overlay integration test using only a synthetic Qt window.

No external application is controlled. A small visible test window runs for six
seconds and writes diagnostics. Run on an unlocked Windows desktop.
"""
from pathlib import Path
import sys
import json
import time
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from map_overlay import native
native.enable_dpi_awareness()

import numpy as np
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QTimer, QRect, Qt
from PySide6.QtGui import QPainter, QColor
from map_overlay.demo import make_demo, mini_at
from map_overlay.capture import CapturePause, TrackingWorker, PreviewWorker, grab
from map_overlay.settings import MinimapPosition
from map_overlay.geometry import transform_points
from map_overlay.widgets import RouteOverlay, qimage

app = QApplication([])
project = make_demo()
parser = argparse.ArgumentParser()
parser.add_argument("--screen", type=int, default=0)
args = parser.parse_args()


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("循图自动验证 · 合成小地图（约六秒后关闭）")
        self.resize(700, 480)
        self.image, _ = mini_at(project.image)
        self.phase = "等待测试窗口进入前台"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111a23"))
        painter.drawImage(QRect(24, 24, 280, 280), qimage(self.image))
        painter.setPen(QColor("#65e7bd"))
        painter.drawText(QRect(330, 40, 340, 180), Qt.TextFlag.TextWordWrap,
                         "系统截屏 → 定位 → 透明窗口叠加\n\n" + self.phase + "\n\n这是合成测试，不操作任何游戏。")


host = Host()
overlay = RouteOverlay()
screen = app.screens()[min(args.screen, len(app.screens())-1)]
host.move(screen.geometry().topLeft().x()+100, screen.geometry().topLeft().y()+100)
host.show()
worker = None
preview_worker = None
capture_pause = CapturePause()
started = 0
metrics = dict(ready_frames=0, blank_rejections=0, resize_rejections=0,
               max_center_error=0., max_overlay_box_error=0, exclusion_error=None, errors=[],
               screenshot_pixels=None, resumed_pixels=None,
               preview_frames=0, preview_blank_frames=0, preview_resize_rejections=0)
preview_capture_times = []
baseline = None
exit_code = 1


def receive(payload):
    elapsed = payload["time"] - started
    if payload.get("fatal"):
        metrics["errors"].append(payload["match"].reason)
        return
    if capture_pause.paused or payload["capture_revision"] != capture_pause.revision:
        return
    if payload["ready"]:
        metrics["ready_frames"] += 1
        matrix = payload["match"].matrix
        h, w = payload["image"].shape[:2]
        if .3 < elapsed < 1.8 or 3.3 < elapsed < 4.8:
            expected = [560, 485] if elapsed < 2 else [620, 465]
            center = transform_points([[w/2, h/2]], matrix)[0]
            metrics["max_center_error"] = max(metrics["max_center_error"], float(np.linalg.norm(center-expected)))
        try:
            overlay.display(payload["box"], project, matrix)
            actual = native.client_box(int(overlay.winId()))
            metrics["max_overlay_box_error"] = max(metrics["max_overlay_box_error"],
                                                    int(np.abs(np.array(actual)-payload["box"]).max()))
        except Exception as exc:
            metrics["errors"].append(str(exc))
    else:
        overlay.hide()
        if 2.3 < elapsed < 2.9:
            metrics["blank_rejections"] += 1
        if elapsed > 5.2:
            metrics["resize_rejections"] += 1


def receive_preview(payload):
    if payload.get("fatal"):
        metrics["errors"].append(payload["status"])
    elapsed = payload["time"] - started
    if payload["image"] is not None:
        metrics["preview_frames"] += 1
        preview_capture_times.append(payload["capture_ms"])
        if 2.3 < elapsed < 2.9:
            metrics["preview_blank_frames"] += 1
    elif elapsed > 5.2:
        metrics["preview_resize_rejections"] += 1


def begin():
    global worker, preview_worker, started, baseline
    host.phase = "定位与叠加"
    host.update()
    box = native.client_box(int(host.winId()))
    ratio = host.devicePixelRatioF()
    rect = [round(24*ratio), round(24*ratio), round(280*ratio), round(280*ratio)]
    baseline = grab((box[0]+rect[0], box[1]+rect[1], rect[2], rect[3]))
    metrics["device_pixel_ratio"] = ratio
    worker = TrackingWorker(project.image, int(host.winId()), MinimapPosition(rect, box[2:]), 1,
                            capture_pause=capture_pause)
    worker.result.connect(receive)
    started = time.monotonic()
    preview_worker = PreviewWorker(int(host.winId()), MinimapPosition(rect, box[2:]), 2, capture_pause)
    preview_worker.result.connect(receive_preview)
    preview_worker.start()
    worker.start()
    QTimer.singleShot(1100, check_exclusion)
    QTimer.singleShot(2200, blank)
    QTimer.singleShot(3000, relocate)
    QTimer.singleShot(5000, resize)
    QTimer.singleShot(6100, finish)


def check_exclusion():
    if not overlay.isVisible():
        metrics["errors"].append("No visible overlay for capture exclusion check")
        return
    captured = grab(overlay.capture_box)
    # Compare before/after pixels while the host terrain is unchanged.
    metrics["exclusion_error"] = float(np.abs(captured.astype(float)-baseline.astype(float)).mean())
    capture_pause.set_paused(True)
    if not overlay.set_capture_excluded(False):
        metrics["errors"].append("Unable to include the overlay in screenshots")
        capture_pause.set_paused(False)
        return
    QTimer.singleShot(250, check_screenshot)


def changed_pixels():
    difference = np.abs(grab(overlay.capture_box).astype(np.int16) - baseline)
    return int(np.count_nonzero(difference.max(axis=2) > 16))


def check_screenshot():
    metrics["screenshot_pixels"] = changed_pixels()
    restored = overlay.set_capture_excluded(True)
    overlay.hide()
    capture_pause.set_paused(False)
    if not restored:
        metrics["errors"].append("Unable to restore capture exclusion")
    QTimer.singleShot(650, check_resumed_exclusion)


def check_resumed_exclusion():
    if overlay.isVisible():
        metrics["resumed_pixels"] = changed_pixels()
    else:
        metrics["errors"].append("No visible overlay after resuming live tracking")


def blank():
    host.image = np.zeros_like(host.image)
    host.phase = "空白帧：应隐藏路线"
    host.update()


def relocate():
    host.image, _ = mini_at(project.image, center=(620, 465))
    host.phase = "新位置：重新确认定位"
    host.update()


def resize():
    host.resize(760, 500)
    host.phase = "窗口尺寸变化：应拒绝继续叠加"
    host.update()


def finish():
    global exit_code
    worker.stop()
    if not worker.wait(5000):
        metrics["errors"].append("Worker failed to stop")
    preview_worker.stop()
    if not preview_worker.wait(5000):
        metrics["errors"].append("Preview worker failed to stop")
    overlay.close()
    host.close()
    passed = (metrics["ready_frames"] >= 5 and metrics["blank_rejections"] >= 2 and
              metrics["resize_rejections"] >= 2 and metrics["max_center_error"] < 3 and
              metrics["max_overlay_box_error"] <= 1 and
              metrics["exclusion_error"] is not None and metrics["exclusion_error"] < 2 and
              metrics["resumed_pixels"] is not None and metrics["resumed_pixels"] <= 100 and
              metrics["screenshot_pixels"] is not None and
              metrics["screenshot_pixels"] > metrics["resumed_pixels"] + 100 and
              metrics["preview_frames"] >= 8 and metrics["preview_blank_frames"] >= 1 and
              metrics["preview_resize_rejections"] >= 1 and not metrics["errors"])
    metrics["preview_capture_ms_mean"] = float(np.mean(preview_capture_times)) if preview_capture_times else None
    metrics["preview_capture_ms_max"] = max(preview_capture_times, default=None)
    metrics["passed"] = passed
    destination = Path(__file__).resolve().parents[1] / ".cache/live-smoke.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(metrics, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=True, indent=2))
    exit_code = 0 if passed else 1
    app.quit()


focus_deadline = time.monotonic() + 45


def wait_for_focus():
    if native.foreground_is(int(host.winId())):
        begin()
    elif time.monotonic() < focus_deadline:
        QTimer.singleShot(100, wait_for_focus)
    else:
        print("The synthetic test window did not enter the foreground.", file=sys.stderr)
        app.quit()


native.focus(int(host.winId()))
QTimer.singleShot(350, wait_for_focus)
app.exec()
sys.exit(exit_code)
