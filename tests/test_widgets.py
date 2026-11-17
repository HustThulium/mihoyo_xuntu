import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest

from map_overlay.demo import make_demo
from map_overlay.widgets import ImageView, CalibrationDialog, RegionDialog


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project = make_demo()

    def test_freehand_stroke_uses_image_coordinates_after_fit(self):
        view = ImageView(self.project.image, "draw")
        view.resize(650, 500)
        view.show()
        self.app.processEvents()
        first = view.mapFromScene(400, 350)
        middle = view.mapFromScene(480, 450)
        last = view.mapFromScene(600, 500)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(view.viewport(), middle, delay=10)
        QTest.mouseMove(view.viewport(), last, delay=10)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
        self.assertEqual(len(view.strokes), 1)
        np.testing.assert_allclose(view.strokes[0][0], [400, 350], atol=3)
        np.testing.assert_allclose(view.strokes[0][-1], [600, 500], atol=3)
        view.undo()
        self.assertEqual(view.strokes, [])
        view.close()

    def test_landmark_pairing_and_error_check(self):
        dialog = CalibrationDialog(self.project.image, self.project.image)
        for x, y in [(400, 200), (700, 250), (480, 600)]:
            dialog.pick_reference(x, y)
            dialog.pick_query((x-300)/2, (y-100)/2)
        self.assertTrue(dialog.ok.isEnabled())
        np.testing.assert_allclose(dialog.matrix, [[2, 0, 300], [0, 2, 100]], atol=1e-8)
        dialog.undo()
        self.assertFalse(dialog.ok.isEnabled())
        dialog.close()

    def test_region_rejects_too_small_minimap(self):
        dialog = RegionDialog(self.project.image, mini=True)
        dialog.selected([20, 20, 40, 40])
        self.assertFalse(dialog.ok.isEnabled())
        dialog.selected([20, 20, 280, 280])
        self.assertTrue(dialog.ok.isEnabled())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
