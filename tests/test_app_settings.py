import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import call, patch, Mock

import numpy as np
import cv2
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt, QUrl, Slot
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPalette, QTransform
from PySide6.QtTest import QTest

from map_overlay import APP_NAME, __version__, native
from map_overlay.app import MainWindow
from map_overlay.project import Marker, Project, decode_asset
from map_overlay.settings import MinimapPosition, MinimapSettings
from map_overlay.vision import Match


class NativeHotkeyTests(unittest.TestCase):
    def test_global_shortcuts_register_overlay_actions(self):
        with patch.object(native.user32, "RegisterHotKey", return_value=True) as register:
            self.assertEqual(native.register_hotkeys(123), [2, 4])
        self.assertEqual(register.call_args_list, [
            call(123, 2, 0x4003, 0x78),
            call(123, 4, 0x4003, 0x7A),
        ])


class AppSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "settings.json"
        def preview_worker(target, position, generation, capture_pause):
            return Mock(hwnd=target, position=position, generation=generation, capture_pause=capture_pause)
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

    def project_with_marker(self):
        icon = np.full((4, 4, 4), 255, dtype=np.uint8)
        asset = decode_asset(cv2.imencode(".png", icon)[1].tobytes())
        return Project(self.image, markers=[Marker(asset, 10, 10)])

    def test_ink_slider_renders_while_dragging_and_remembers_release(self):
        window = self.window
        black = np.zeros((100, 100, 3), np.uint8)
        ink = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        ink.fill(QColor("red"))
        asset = self.project_with_marker().markers[0].asset
        window.project = Project(black, ink=ink, markers=[Marker(asset, 72, 50, 8)])
        window.update_preview(black, np.eye(2, 3))
        overlay = window.overlay
        overlay.project = window.project
        overlay.matrix = QTransform()
        overlay.resize(100, 100)
        overlay.pixel_size = (100, 100)

        def check_render(red):
            preview = window.preview.pixmap().toImage()
            self.assertAlmostEqual(preview.pixelColor(102, 102).red(), red, delta=1)
            frame = overlay.grab().toImage()
            self.assertAlmostEqual(frame.pixelColor(50, 50).alpha(), red, delta=1)
            self.assertEqual(frame.pixelColor(72, 50), QColor("white"))

        slider = window.ink_slider
        self.assertEqual(slider.value(), 70)
        check_render(179)
        slider.setSliderDown(True)
        slider.setSliderPosition(100)
        check_render(255)
        self.assertEqual(window.ink_percentage.text(), "100%")
        self.assertEqual(window.minimap_settings.ink_opacity, 70)
        slider.setSliderDown(False)
        self.assertEqual(MinimapSettings(self.path).ink_opacity, 100)
        slider.setValue(0)
        check_render(0)
        window.minimap_settings.save_position(self.position)
        self.assertEqual(MinimapSettings(self.path).ink_opacity, 0)
        self.assertEqual(ink.pixelColor(50, 50), QColor("red"))
        self.assertFalse(window.dirty)

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
        self.window.project = self.project_with_marker()
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
        self.window.project = self.project_with_marker()
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

    def test_preview_starts_collapsed_then_expands_on_first_position_only(self):
        window = self.window
        self.assertTrue(window.preview_tray.isHidden())
        window.save_minimap_position(self.position, self.selected_image())
        self.assertTrue(window.preview_button.isChecked())
        self.assertFalse(window.preview_tray.isHidden())
        preview_worker = window.preview_worker
        window.preview_button.click()
        self.assertTrue(window.preview_tray.isHidden())
        self.assertIs(window.preview_worker, preview_worker)
        window.save_minimap_position(self.position, self.selected_image())
        self.assertTrue(window.preview_tray.isHidden())

    def test_theme_updates_open_windows_and_persists_without_changing_minimap_settings(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        self.window.save_ink_opacity(35)
        settings = self.window.settings_dialog
        picker = settings.theme_picker
        self.assertEqual(picker.currentData(), "light")
        role = QPalette.ColorRole.Window
        light = self.app.palette().color(role)
        picker.setCurrentIndex(picker.findData("dark"))
        dark = self.app.palette().color(role)
        self.assertNotEqual(dark, light)
        self.assertEqual(settings.palette().color(role), dark)

        with patch("map_overlay.settings.os.replace", side_effect=PermissionError("locked")), \
                patch.object(self.window, "error") as error:
            picker.setCurrentIndex(picker.findData("light"))
        error.assert_called_once()
        self.assertEqual(picker.currentData(), "dark")
        self.assertEqual(self.app.palette().color(role), dark)

        self.window.toggle_preview(False)
        restarted = MinimapSettings(self.path)
        self.assertEqual(restarted.theme, "dark")
        self.assertEqual(restarted.position, self.position)
        self.assertEqual(restarted.ink_opacity, 35)
        self.assertFalse(restarted.preview_enabled)

        window = MainWindow(settings_path=self.path)
        try:
            self.assertEqual(window.settings_dialog.theme_picker.currentData(), "dark")
            self.assertEqual(window.palette().color(role), dark)
        finally:
            window.close()
            window.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_preview_toggle_is_independent_of_tracking_and_survives_restart(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        preview = self.window.preview_worker
        self.window.project = self.project_with_marker()
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

    def test_settings_entry_switch_and_reopening_preserve_saved_preference(self):
        dialog = self.window.settings_dialog
        observed = []

        def edit_settings():
            observed.append(dialog.isVisible())
            observed.append(self.window.preview_toggle.window() is dialog)
            QTest.mouseClick(dialog.preview_toggle, Qt.MouseButton.LeftButton)
            dialog.reject()

        QTimer.singleShot(0, edit_settings)
        self.window.settings_button.click()
        self.assertEqual(observed, [True, True])
        self.assertFalse(MinimapSettings(self.path).preview_enabled)
        self.assertFalse(dialog.preview_toggle.isChecked())
        # Merely reopening/closing the panel must neither reset nor save settings.
        with patch.object(self.window.minimap_settings, "save_preview_enabled") as save:
            QTimer.singleShot(0, dialog.reject)
            self.window.open_settings()
        save.assert_not_called()
        self.assertFalse(dialog.preview_toggle.isChecked())

    def test_position_entry_is_independent_of_program_settings(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        dialog = self.window.settings_dialog
        observed = []

        def choose_position():
            position_dialog = self.app.activeModalWidget()
            observed.append(position_dialog.windowTitle())
            observed.append(dialog.isVisible())
            position_dialog.select_button.click()

        with patch.object(self.window, "select_minimap") as select:
            QTimer.singleShot(0, choose_position)
            self.window.minimap_position_button.click()
            select.assert_called_once()
            # Program preferences must never trigger the minimap position workflow.
            QTimer.singleShot(0, dialog.reject)
            self.window.open_settings()
            select.assert_called_once()
        self.assertEqual(observed, ["小地图位置设置", False])
        self.assertEqual(list(dialog.category_indexes), ["general", "appearance", "about"])

    def test_about_shows_release_version_and_opens_the_displayed_links(self):
        dialog = self.window.settings_dialog
        dialog.show()
        about = dialog.categories.item(dialog.category_indexes["about"])
        QTest.mouseClick(dialog.categories.viewport(), Qt.MouseButton.LeftButton,
                         pos=dialog.categories.visualItemRect(about).center())
        self.app.processEvents()

        self.assertEqual(about.text(), "关于")
        self.assertEqual(dialog.pages.currentIndex(), dialog.category_indexes["about"])
        page = dialog.pages.currentWidget()
        labels = page.findChildren(QLabel)
        self.assertEqual(page.findChild(QLabel, "aboutAppName").text(), APP_NAME)
        self.assertTrue(any(f"版本 {__version__}" in label.text() for label in labels))
        icons = page.findChildren(QLabel, "aboutLinkIcon")
        self.assertEqual(len(icons), 2)
        self.assertTrue(all(not icon.pixmap().isNull() for icon in icons))
        links = [label for label in labels if label.openExternalLinks()]
        urls = ["https://github.com/HustThulium/mihoyo_xuntu", "https://space.bilibili.com/603381"]
        self.assertEqual(len(links), len(urls))
        for link, url in zip(links, urls):
            self.assertIn(f">{url}</a>", link.text())

        opened = []

        class Browser(QObject):
            @Slot(QUrl)
            def open(self, url):
                opened.append(url.toString())

        browser = Browser()
        QDesktopServices.setUrlHandler("https", browser, "open")
        try:
            for link in links:
                QTest.mouseClick(link, Qt.MouseButton.LeftButton, pos=QPoint(8, link.height() // 2))
        finally:
            QDesktopServices.unsetUrlHandler("https")
        self.assertEqual(opened, urls)
        self.assertFalse(self.path.exists())

        picker = dialog.theme_picker
        original_color = links[0].palette().color(QPalette.ColorRole.Link).name()
        picker.setCurrentIndex(picker.findData("dark"))
        updated_color = links[0].palette().color(QPalette.ColorRole.Link).name()
        self.assertNotEqual(updated_color, original_color)
        self.assertTrue(all(f'color:{updated_color}' in link.text() for link in links))

    def test_window_preference_switch_persists_and_rolls_back_failed_save(self):
        toggle = self.window.settings_dialog.minimize_toggle
        self.window.settings_dialog.show()
        self.app.processEvents()
        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        self.assertFalse(MinimapSettings(self.path).minimize_on_tracking)
        with patch("map_overlay.settings.os.replace", side_effect=PermissionError("locked")), \
                patch.object(self.window, "error") as error:
            QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        self.assertFalse(toggle.isChecked())
        self.assertFalse(self.window.minimap_settings.minimize_on_tracking)
        self.assertFalse(MinimapSettings(self.path).minimize_on_tracking)
        error.assert_called_once()
        self.window.settings_dialog.close()
        restarted = MainWindow(settings_path=self.path)
        try:
            self.assertFalse(restarted.settings_dialog.minimize_toggle.isChecked())
        finally:
            restarted.close()
            restarted.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_tracking_respects_window_preference_and_preserves_maximization(self):
        self.window.save_minimap_position(self.position, self.selected_image())
        self.window.project = self.project_with_marker()
        with patch("map_overlay.app.TrackingWorker") as worker, patch("map_overlay.app.native.focus") as focus:
            for minimize in (True, False):
                self.window.settings_dialog.minimize_toggle.setChecked(minimize)
                for maximized in (True, False):
                    with self.subTest(minimize=minimize, maximized=maximized):
                        if maximized:
                            self.window.showMaximized()
                        else:
                            self.window.showNormal()
                        self.app.processEvents()
                        geometry = self.window.normalGeometry()
                        focus.reset_mock()
                        worker.return_value.start.reset_mock()
                        self.window.track_button.click()
                        try:
                            self.app.processEvents()
                            worker.return_value.start.assert_called_once()
                            self.assertEqual(self.window.isMinimized(), minimize)
                            if minimize:
                                focus.assert_called_once_with(self.window.targets.currentData())
                            else:
                                focus.assert_not_called()
                                self.assertTrue(self.window.isVisible())
                                self.assertEqual(self.window.isMaximized(), maximized)
                        finally:
                            self.window.track_button.click()
                        self.app.processEvents()
                        self.assertFalse(self.window.isMinimized())
                        self.assertEqual(self.window.isMaximized(), maximized)
                        self.assertEqual(self.window.normalGeometry(), geometry)

    def test_screenshot_switch_waits_for_a_fresh_pose_and_restores_capture(self):
        window = self.window
        window.save_minimap_position(self.position, self.selected_image())
        window.project = self.project_with_marker()
        window.minimap_settings.minimize_on_tracking = False
        affinity_calls = []

        def set_capture_excluded(excluded):
            affinity_calls.append((excluded, window.capture_pause.paused))
            return True

        with patch("map_overlay.app.TrackingWorker") as tracking, \
                patch("map_overlay.app.native.foreground_is", return_value=False) as foreground, \
                patch("map_overlay.widgets.logical_geometry", return_value=(window.rect(), 1.0)), \
                patch.object(window.overlay, "set_capture_excluded", side_effect=set_capture_excluded):
            tracking.return_value.hwnd = 123
            tracking.return_value.position = self.position
            window.toggle_tracking()
            window.screenshot_button.click()
            self.assertTrue(window.screenshot_button.isChecked())
            self.assertFalse(window.capture_pause.paused)
            self.assertIn("等待定位", window.screenshot_button.text())

            foreground.return_value = True
            frame = dict(generation=window.generation, time=time.monotonic(), ready=True,
                         match=Match(matrix=np.eye(2, 3)), image=self.selected_image(), box=(120, 130, 100, 100))
            window.receive_frame(frame)
            self.assertTrue(window.overlay.isVisible())
            self.assertTrue(window.capture_pause.paused)
            self.assertIn("路线已冻结", window.state.text())
            self.assertIs(tracking.call_args.args[-1], window.capture_pause)
            self.assertIs(window.preview_worker.capture_pause, window.capture_pause)
            self.assertEqual(affinity_calls, [(True, False), (False, True)])
            read = Mock()
            window.capture_pause.capture(read)
            read.assert_not_called()
            window.receive_frame(dict(frame, ready=False))
            self.assertTrue(window.overlay.isVisible())

            window.screenshot_button.click()
            self.assertFalse(window.capture_pause.paused)
            self.assertFalse(window.screenshot_button.isChecked())
            self.assertIn("正在恢复定位", window.state.text())
            self.assertEqual(affinity_calls[-1], (True, True))
            self.assertFalse(window.overlay.isVisible())
            window.receive_frame(frame)
            self.assertFalse(window.overlay.isVisible())
            window.receive_frame(dict(frame, capture_revision=window.capture_pause.revision))
            self.assertTrue(window.overlay.isVisible())

    def test_screenshot_mode_stays_on_when_game_moves_or_loses_focus(self):
        window = self.window
        window.save_minimap_position(self.position, self.selected_image())
        window.project = self.project_with_marker()
        window.minimap_settings.minimize_on_tracking = False
        with patch("map_overlay.app.TrackingWorker") as tracking, \
                patch("map_overlay.app.native.client_box", return_value=(100, 100, 640, 480)) as client, \
                patch("map_overlay.app.native.foreground_is", return_value=True) as foreground, \
                patch("map_overlay.widgets.logical_geometry", return_value=(window.rect(), 1.0)), \
                patch.object(window.overlay, "set_capture_excluded", side_effect=[True, True, True, False]) as affinity, \
                patch.object(window.overlay, "destroy", side_effect=lambda: self.assertTrue(window.capture_pause.paused)) as destroy:
            tracking.return_value.hwnd = 123
            tracking.return_value.position = self.position
            window.toggle_tracking()
            window.receive_frame(dict(generation=window.generation, time=time.monotonic(), ready=True,
                                      match=Match(matrix=np.eye(2, 3)), image=self.selected_image(),
                                      box=(120, 130, 100, 100)))
            window.toggle_screenshot_mode()
            self.assertTrue(window.capture_pause.paused)

            client.return_value = (125, 100, 640, 480)
            window.expire_overlay()
            self.assertEqual(window.overlay.capture_box, (145, 130, 100, 100))

            foreground.return_value = False
            window.last_visible = time.monotonic() - 30
            window.expire_overlay()
            self.assertFalse(window.overlay.isVisible())
            self.assertTrue(window.capture_pause.paused)
            self.assertTrue(window.screenshot_button.isChecked())

            client.return_value = (125, 100, 800, 600)
            foreground.return_value = True
            window.expire_overlay()
            self.assertFalse(window.overlay.isVisible())
            self.assertTrue(window.capture_pause.paused)
            client.return_value = (125, 100, 640, 480)
            window.expire_overlay()
            affinity.assert_called_with(False)
            self.assertTrue(window.overlay.isVisible())

            window.toggle_screenshot_mode()
            destroy.assert_called_once()
            self.assertFalse(window.capture_pause.paused)
            self.assertFalse(window.overlay.isVisible())
            self.assertFalse(window.screenshot_button.isChecked())

    def test_new_capture_opens_cropped_region_in_main_editor(self):
        with patch("map_overlay.app.QTimer.singleShot", side_effect=lambda delay, callback: callback()), \
                patch("map_overlay.app.native.foreground_is", return_value=True), \
                patch("map_overlay.app.grab", return_value=self.image), \
                patch("map_overlay.app.RegionDialog") as selector, \
                patch("map_overlay.app.QInputDialog.getText", return_value=(" 测试区域 ", True)):
            selector.return_value.exec.return_value = 1
            selector.return_value.rect = [20, 30, 250, 200]
            self.window.new_capture()

        project = self.window.project
        np.testing.assert_array_equal(project.image, self.image[30:230, 20:270])
        self.assertEqual(project.name, "测试区域")
        self.assertEqual(project.target_title, "测试窗口")
        self.assertFalse(project.has_annotations)
        self.assertIs(self.window.canvas.project, project)
        self.assertIsNone(self.window.project_path)
        self.assertTrue(self.window.dirty)
        self.assertTrue(self.window.isVisible())

    def test_capture_return_preserves_maximized_window(self):
        self.window.showMaximized()
        self.app.processEvents()
        with patch("map_overlay.app.QTimer.singleShot", side_effect=lambda delay, callback: callback()), \
                patch("map_overlay.app.native.foreground_is", return_value=True), \
                patch("map_overlay.app.grab", return_value=self.image):
            received = Mock()
            self.window.capture_action(received)
        self.app.processEvents()
        received.assert_called_once()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMaximized())
        self.assertFalse(self.window.isMinimized())


if __name__ == "__main__":
    unittest.main()
