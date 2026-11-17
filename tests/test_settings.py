from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from map_overlay.settings import MinimapPosition, MinimapSettings


class MinimapSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "本机设置" / "settings.json"
        self.position = MinimapPosition([20, 30, 280, 280], [1920, 1080])

    def test_first_use_then_restart_and_window_move(self):
        settings = MinimapSettings(self.path)
        self.assertIn("首次使用", settings.status())
        with self.assertRaisesRegex(ValueError, "尚未设置"):
            settings.require_position([1920, 1080])
        settings.save_position(self.position)
        restarted = MinimapSettings(self.path)
        self.assertEqual(restarted.require_position([1920, 1080]), self.position)
        self.assertEqual(restarted.position.absolute_box((-1920, 100, 1920, 1080)),
                         (-1900, 130, 280, 280))
        with self.assertRaisesRegex(ValueError, "尺寸"):
            restarted.require_position([2560, 1440])
        with self.assertRaisesRegex(ValueError, "尺寸"):
            restarted.position.absolute_box((0, 0, 2560, 1440))
        # A temporary resize does not destroy the saved configuration.
        self.assertEqual(restarted.require_position([1920, 1080]), self.position)

    def test_corrupt_or_invalid_settings_can_be_replaced(self):
        self.path.parent.mkdir()
        invalid = ["{", "[]", '{"version": 2}',
                   '{"version": 1, "minimap": {"rect": [20, 30, 280, 280], "client_size": [100, 100]}}']
        for content in invalid:
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                settings = MinimapSettings(self.path)
                self.assertIsNone(settings.position)
                self.assertIn("无法读取", settings.status())
                with self.assertRaises(ValueError):
                    settings.require_position([1920, 1080])
                settings.save_position(self.position)
                self.assertIsNone(settings.load_error)
                self.assertEqual(MinimapSettings(self.path).position, self.position)

    def test_failed_save_preserves_previous_settings_and_cleans_temporary(self):
        settings = MinimapSettings(self.path)
        settings.save_position(self.position)
        replacement = MinimapPosition([40, 50, 280, 280], [1920, 1080])
        with patch("map_overlay.settings.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                settings.save_position(replacement)
        self.assertEqual(settings.position, self.position)
        self.assertEqual(MinimapSettings(self.path).position, self.position)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_coordinates_never_become_a_capture_box(self):
        for rect, size in [([True, 0, 100, 100], [640, 480]),
                           ([-1, 0, 100, 100], [640, 480]),
                           ([600, 0, 100, 100], [640, 480]),
                           ([0, 0, 20, 20], [640, 480]),
                           ([0, 0, 100, 100], None)]:
            with self.subTest(rect=rect, size=size), self.assertRaises(ValueError):
                MinimapPosition(rect, size)

    def test_preview_preference_before_position_and_across_position_changes(self):
        settings = MinimapSettings(self.path)
        self.assertTrue(settings.preview_enabled)
        settings.save_preview_enabled(False)
        restarted = MinimapSettings(self.path)
        self.assertFalse(restarted.preview_enabled)
        self.assertIsNone(restarted.position)
        self.assertIsNone(restarted.load_error)
        restarted.save_position(self.position)
        self.assertFalse(MinimapSettings(self.path).preview_enabled)
        restarted.save_preview_enabled(True)
        self.assertEqual(MinimapSettings(self.path).position, self.position)

    def test_existing_position_settings_default_to_enabled_preview(self):
        self.path.parent.mkdir()
        self.path.write_text('{"version": 1, "minimap": {"rect": [20, 30, 280, 280], "client_size": [1920, 1080]}}',
                             encoding="utf-8")
        settings = MinimapSettings(self.path)
        self.assertTrue(settings.preview_enabled)
        self.assertEqual(settings.position, self.position)


if __name__ == "__main__":
    unittest.main()
