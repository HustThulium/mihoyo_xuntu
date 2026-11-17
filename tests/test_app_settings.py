import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent

from map_overlay.app import MainWindow
from map_overlay.project import Project
from map_overlay.settings import MinimapPosition, MinimapSettings
from map_overlay.vision import Match


class AppSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "settings.json"
        def preview_worker(target, position, generation):
            return Mock(hwnd=target, position=position, generation=generation)
        patcher = patch("map_overlay.app.PreviewWorker", side_effect=preview_worker)
        self.preview_workers = patcher.start()
        self.addCleanup(patcher.stop)
        for name, value in [("windows", []), ("register_hotkeys", []),
                            ("unregister_hotkeys", None), ("client_box", (100, 100, 640, 480)),
                            ("monitor_for", None), ("focus", None)]:
            patcher = patch(f"map_overlay.app.native.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = MainWindow(settings_path=self.path)
        self.window.targets.addItem("测试窗口", 123)
        self.window.targets.setCurrentIndex(1)
        self.addCleanup(self.close_window)
        self.image = np.zeros((480, 640, 3), dtype=np.uint8)
        self.position = MinimapPosition([20, 30, 100, 100], [640, 480])
        self.image[30:130, 20:120] = [35, 120, 210]

    def selected_image(self):
        x, y, w, h = self.position.rect
        return self.image[y:y+h, x:x+w]

    def assert_selected_preview(self):
        np.testing.assert_array_equal(self.window.last_preview, self.selected_image())
        self.assertIsNone(self.window.last_preview_matrix)
        pixmap = self.window.preview.pixmap()
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.toImage().pixelColor(100, 100).getRgb()[:3], (210, 120, 35))

    def close_window(self):
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_setting_without_project_then_switching_maps_and_restart(self):
        with patch.object(self.window, "capture_action", side_effect=lambda callback: callback(self.image, (100, 100, 640, 480))), \
                patch("map_overlay.app.RegionDialog") as selector:
            selector.return_value.exec.return_value = 1
            selector.return_value.rect = list(self.position.rect)
            self.window.select_minimap()
        self.assertIsNone(self.window.project)
        self.assertFalse(self.window.dirty)
        self.assertEqual(MinimapSettings(self.path).position, self.position)
        self.assert_selected_preview()
        self.assertIn("设置成功", self.window.preview_caption.text())
        self.assertIn("设置成功", self.window.state.text())
        with patch("map_overlay.app.Localizer"):
            for project in [Project(self.image, "区域一"), Project(self.image, "区域二")]:
                self.window.set_project(project, "区域.routepack")
                self.assertFalse(self.window.dirty)
                self.assertEqual(self.window.minimap_settings.position, self.position)
                self.assertEqual(self.window.mini_from_capture(self.image, (-500, 50, 640, 480)).shape,
                                 (100, 100, 3))
                self.assert_selected_preview()
        restarted = MainWindow(settings_path=self.path)
        try:
            self.assertEqual(restarted.minimap_settings.position, self.position)
        finally:
            restarted.close()
            restarted.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_cancelled_selection_preserves_position_and_project(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        project = Project(self.image)
        self.window.project = project
        self.window.seed = "existing calibration"
        with patch.object(self.window, "capture_action", side_effect=lambda callback: callback(self.image, (100, 100, 640, 480))), \
                patch("map_overlay.app.RegionDialog") as selector:
            selector.return_value.exec.return_value = 0
            self.window.select_minimap()
        self.assertEqual(self.window.seed, "existing calibration")
        self.assertFalse(self.window.dirty)
        self.assertIs(self.window.project, project)
        self.assertEqual(MinimapSettings(self.path).position, self.position)
        self.assert_selected_preview()

    def test_new_position_replaces_preview_and_resets_calibration(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        self.window.project = Project(self.image)
        self.window.localizer = Mock()
        self.window.seed = "old calibration"
        replacement = MinimapPosition([200, 200, 100, 100], [640, 480])
        replacement_image = np.full((100, 100, 3), [40, 80, 160], dtype=np.uint8)
        self.window.save_minimap_position(replacement, replacement_image)
        self.assertEqual(MinimapSettings(self.path).position, replacement)
        np.testing.assert_array_equal(self.window.last_preview, replacement_image)
        self.assertIsNone(self.window.last_preview_matrix)
        self.assertIsNone(self.window.seed)
        self.window.localizer.reset.assert_called_once()
        self.assertFalse(self.window.dirty)

    def test_tracking_uses_local_position_and_rejects_resized_window(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        self.window.project = Project(self.image, strokes=[[[0, 0], [20, 20]]])
        with patch("map_overlay.app.TrackingWorker") as worker, patch.object(self.window, "showMinimized"):
            self.window.toggle_tracking()
            self.assertEqual(worker.call_args.args[2], self.position)
            worker.return_value.start.assert_called_once()
            self.window.stop_tracking(show=False)
            with patch("map_overlay.app.native.client_box", return_value=(100, 100, 800, 600)), \
                    patch.object(self.window, "error") as error:
                self.window.toggle_tracking()
            self.assertIn("尺寸", str(error.call_args.args[0]))
            self.assertEqual(worker.call_count, 1)

    def test_region_alone_does_not_enable_tracking(self):
        self.window.project = Project(self.image, strokes=[[[0, 0], [20, 20]]])
        with patch("map_overlay.app.TrackingWorker") as worker, patch.object(self.window, "error") as error:
            self.window.toggle_tracking()
        worker.assert_not_called()
        self.assertIn("尚未设置", str(error.call_args.args[0]))
        self.assertFalse(self.path.exists())

    def test_failed_save_keeps_previous_preview_and_position(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        replacement = MinimapPosition([200, 200, 100, 100], [640, 480])
        with patch("map_overlay.settings.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                self.window.save_minimap_position(replacement, np.zeros((100, 100, 3), dtype=np.uint8))
        self.assert_selected_preview()
        self.assertEqual(self.window.minimap_settings.position, self.position)

    def preview_payload(self, **changes):
        payload = dict(generation=self.window.preview_generation, image=self.selected_image(),
                       time=time.monotonic(), box=(120, 130, 100, 100), status="实时同步")
        payload.update(changes)
        return payload

    def test_preview_works_without_region_and_ignores_stale_or_moved_frames(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        self.assertIsNone(self.window.project)
        self.assertIsNone(self.window.worker)
        self.assertIsNotNone(self.window.preview_worker)
        self.window.state.setText("设置状态")
        with patch("map_overlay.app.native.foreground_is", return_value=True):
            self.window.receive_preview(self.preview_payload())
            self.assert_selected_preview()
            self.assertEqual(self.window.preview_caption.text(), "实时同步")
            self.assertEqual(self.window.state.text(), "设置状态")
            for changes in [dict(time=time.monotonic() - 2), dict(box=(130, 130, 100, 100))]:
                self.window.receive_preview(self.preview_payload(**changes))
                self.assertIn("暂停", self.window.preview_caption.text())
        previous = self.window.preview_caption.text()
        self.window.receive_preview(self.preview_payload(generation=self.window.preview_generation - 1))
        self.assertEqual(self.window.preview_caption.text(), previous)

    def test_preview_toggle_is_independent_of_tracking_and_survives_restart(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        preview = self.window.preview_worker
        self.window.project = Project(self.image, strokes=[[[0, 0], [20, 20]]])
        with patch("map_overlay.app.TrackingWorker") as worker, patch.object(self.window, "showMinimized"):
            self.window.toggle_tracking()
            self.assertIs(self.window.preview_worker, preview)
            self.window.preview_toggle.setChecked(False)
            self.assertIsNone(self.window.preview_worker)
            self.assertIs(self.window.worker, worker.return_value)
            worker.return_value.stop.assert_not_called()
            previous = self.window.last_preview.copy()
            self.window.receive_frame(dict(generation=self.window.generation, match=Match(),
                                           ready=False, time=time.monotonic(), image=np.zeros_like(previous)))
            np.testing.assert_array_equal(self.window.last_preview, previous)
            self.window.preview_toggle.setChecked(True)
            preview = self.window.preview_worker
            self.window.stop_tracking(show=False)
            self.assertIs(self.window.preview_worker, preview)
            preview.stop.assert_not_called()
            self.window.preview_toggle.setChecked(False)
        restarted = MainWindow(settings_path=self.path)
        try:
            self.assertFalse(restarted.preview_toggle.isChecked())
        finally:
            restarted.close()
            restarted.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_switching_window_rejects_old_preview_source(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        previous = self.window.preview_worker
        payload = self.preview_payload()
        self.window.targets.addItem("窗口二", 456)
        self.window.targets.setCurrentIndex(2)
        previous.stop.assert_called_once()
        self.assertEqual(self.window.preview_worker.hwnd, 456)
        self.window.receive_preview(payload)
        self.assertIsNone(self.window.live_preview_image)

    def test_failed_toggle_preserves_switch_and_worker(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        worker = self.window.preview_worker
        with patch("map_overlay.settings.os.replace", side_effect=PermissionError("locked")), \
                patch.object(self.window, "error") as error:
            self.window.preview_toggle.setChecked(False)
        self.assertTrue(self.window.preview_toggle.isChecked())
        self.assertIs(self.window.preview_worker, worker)
        error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
