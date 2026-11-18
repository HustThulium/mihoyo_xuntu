import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QRect, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QTransform
from PySide6.QtTest import QTest

from map_overlay.annotations import paint_minimap
from map_overlay.demo import make_demo
from map_overlay.project import Marker, Project
from map_overlay.widgets import AnnotationView, CalibrationDialog, DrawingOverlay, RegionDialog


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project = make_demo()

    def test_freehand_stroke_uses_image_coordinates_after_fit(self):
        project = Project(self.project.image, color="#ff0000")
        view = self.editor(project)
        self.draw_line(view, (400, 350), (600, 350))
        self.assertEqual(project.ink.pixelColor(500, 350), QColor("#ff0000"))
        project.color = "#0000ff"
        self.draw_line(view, (500, 260), (500, 450))
        self.assertEqual(project.ink.pixelColor(500, 350), QColor("#0000ff"))
        self.assertEqual(project.ink.pixelColor(430, 350), QColor("#ff0000"))
        view.undo()
        self.assertEqual(project.ink.pixelColor(500, 350), QColor("#ff0000"))
        self.assertEqual(project.ink.pixelColor(500, 300).alpha(), 0)
        view.undo()
        self.assertIsNone(project.ink)

    def editor(self, project):
        view = AnnotationView()
        self.addCleanup(view.close)
        view.set_project(project)
        view.resize(650, 500)
        view.show()
        self.app.processEvents()
        return view

    def draw_line(self, view, start, end):
        first = view.mapFromScene(*start)
        last = view.mapFromScene(*end)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
        QTest.mouseMove(view.viewport(), last, delay=10)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)

    def test_png_can_be_placed_twice_then_moved_resized_deleted_and_restored(self):
        project = Project(self.project.image)
        asset = self.project.markers[0].asset
        view = self.editor(project)
        view.set_mode("marker")
        view.prepare_marker(asset)
        for x, y in [(400, 350), (600, 450)]:
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(x, y))
        self.assertEqual(len(project.markers), 2)
        self.assertIs(project.markers[0].asset, project.markers[1].asset)
        original = project.markers[0]
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.draw_line(view, (400, 350), (510, 360))
        moved = project.markers[0]
        np.testing.assert_allclose((moved.x, moved.y), (510, 360), atol=3)
        view.change_marker_size(48)
        self.assertEqual(project.markers[0].size, 48)
        QTest.keyClick(view, Qt.Key.Key_Delete)
        self.assertEqual(len(project.markers), 1)
        view.undo()
        self.assertEqual(project.markers[0].size, 48)
        view.undo()
        self.assertEqual(project.markers[0], moved)
        view.undo()
        self.assertEqual(project.markers[0], original)

    def test_minimap_keeps_marker_size_at_different_zoom_and_clips_corners(self):
        asset = self.project.markers[0].asset
        project = Project(self.project.image, markers=[Marker(asset, 550, 400, 28),
                                                        Marker(asset, 600, 450, 40)])
        for scale in (0.8, 1.2):
            with self.subTest(scale=scale):
                frame = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
                frame.fill(Qt.GlobalColor.transparent)
                ref_to_view = QTransform(scale, 0, 0, scale, 50 - 550 * scale, 50 - 400 * scale)
                with QPainter(frame) as painter:
                    paint_minimap(painter, project, ref_to_view, QRectF(0, 0, 100, 100))
                self.assertEqual(frame.pixelColor(50, 50), QColor("#ffce73"))
                self.assertGreater(frame.pixelColor(61, 50).alpha(), 0)
                self.assertEqual(frame.pixelColor(65, 50).alpha(), 0)
                self.assertEqual(frame.pixelColor(90, 90).alpha(), 0)

    def test_frozen_capture_keeps_each_stroke_color_and_stages_edits(self):
        project = Project(self.project.image)
        matrix = np.array([[2, 0, 50], [0, 2, 20]], dtype=float)
        with patch("map_overlay.widgets.logical_geometry", return_value=(QRect(0, 0, 320, 200), 1.0)):
            dialog = DrawingOverlay(project.image[:200, :320], (0, 0, 320, 200), project, matrix)
        self.addCleanup(dialog.close)
        QTest.mouseClick(dialog, Qt.MouseButton.LeftButton, pos=QPoint(100, 80))
        with patch("map_overlay.widgets.QColorDialog.getColor", return_value=QColor("#de5317")):
            dialog.choose_color()
        QTest.mouseClick(dialog, Qt.MouseButton.LeftButton, pos=QPoint(150, 100))
        self.assertEqual([stroke.color for stroke in dialog.strokes], [project.color, "#de5317"])
        self.assertEqual(dialog.strokes[0].path.currentPosition().toTuple(), (250.0, 180.0))
        dialog.undo()
        self.assertEqual(len(dialog.strokes), 1)
        dialog.reject()
        self.assertIsNone(project.ink)

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
