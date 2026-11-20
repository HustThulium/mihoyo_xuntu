import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QTransform
from PySide6.QtTest import QTest

from map_overlay.annotations import paint_minimap
from map_overlay.demo import make_demo
from map_overlay.project import INK_FORMAT, Marker, Project, decode_asset, encode_ink
from map_overlay.widgets import AnnotationView, CalibrationDialog, RegionDialog, RouteOverlay


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

    def test_straight_line_previews_latest_endpoint_and_respects_width(self):
        project = Project(np.zeros((192, 256, 3), np.uint8), color="#ff0000")
        view = self.editor(project)
        view.set_ink_only(True)
        view.set_mode("line")
        view.set_ink_width(16)
        viewport = view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(30, 60))
        QTest.mouseMove(viewport, view.mapFromScene(100, 140), delay=10)
        last = view.mapFromScene(210, 60)
        QTest.mouseMove(viewport, last, delay=10)
        self.assertIsNone(project.ink)
        preview = viewport.grab().toImage()
        self.assertEqual(preview.pixelColor(view.mapFromScene(100, 66)), QColor("red"))
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=last)
        self.assertEqual(project.ink.pixelColor(100, 66), QColor("red"))
        self.assertEqual(project.ink.pixelColor(100, 72).alpha(), 0)
        self.assertEqual(project.ink.pixelColor(100, 140).alpha(), 0)
        view.undo()
        self.assertIsNone(project.ink)

    def test_eraser_handles_loaded_and_hand_drawn_ink_as_one_undo_step(self):
        asset = self.project.markers[0].asset
        marker = Marker(asset, 100, 100, 28)
        project = Project(np.zeros((192, 256, 3), np.uint8), color="#ff0000", markers=[marker])
        imported = QImage(180, 12, INK_FORMAT)
        imported.fill(QColor("blue"))
        view = self.editor(project)
        view.edits.merge_ink((30, 98), imported)
        self.draw_line(view, (30, 60), (210, 60))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ink.routepack"
            project.save(path)
            project = Project.load(path)
            view.set_project(project)
            old_ink = QImage(project.ink)
            view.set_ink_only(True)
            view.set_mode("erase")
            view.set_eraser_diameter(24)
            viewport = view.viewport()
            last = view.mapFromScene(100, 136)
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(100, 28))
            QTest.mouseMove(viewport, last, delay=10)
            preview = viewport.grab().toImage()
            self.assertEqual(project.ink, old_ink)
            self.assertIn(preview.pixelColor(view.mapFromScene(100, 103)).name(), ("#465058", "#59636b"))
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=last)
            for y in (60, 103):
                self.assertEqual(project.ink.pixelColor(100, y).alpha(), 0)
                self.assertEqual(project.ink.pixelColor(45, y), old_ink.pixelColor(45, y))
            project.save(path)
            saved = Project.load(path)
            self.assertEqual(saved.ink, project.ink)
            self.assertEqual(saved.markers[0].asset.png, asset.png)
            self.assertEqual((saved.markers[0].x, saved.markers[0].y), (100, 100))

        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(100, 170))
        view.undo()
        self.assertEqual(project.ink, old_ink)
        view.set_eraser_diameter(512)
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=view.mapFromScene(128, 96))
        self.assertIsNone(project.ink)
        view.undo()
        self.assertEqual(project.ink, old_ink)

    def test_ink_only_hides_map_and_marker_without_moving_the_view(self):
        asset = self.project.markers[0].asset
        image = np.zeros((192, 256, 3), np.uint8)
        project = Project(image, color="#ff0000", markers=[Marker(asset, 128, 96)])
        view = self.editor(project)
        self.draw_line(view, (60, 60), (180, 60))
        view.scale(1.5, 1.5)
        view.centerOn(142, 110)
        transform = view.viewportTransform()
        marker_point = view.mapFromScene(128, 96)
        ink_point = view.mapFromScene(110, 60)
        map_point = view.mapFromScene(100, 110)
        composite = view.viewport().grab().toImage()
        self.assertEqual(composite.pixelColor(marker_point), QColor("#ffce73"))
        self.assertEqual(composite.pixelColor(map_point), QColor("black"))
        view.set_ink_only(True)
        ink_only = view.viewport().grab().toImage()
        self.assertEqual(view.viewportTransform(), transform)
        self.assertEqual(ink_only.pixelColor(ink_point), QColor("red"))
        for point in (marker_point, map_point):
            self.assertIn(ink_only.pixelColor(point).name(), ("#465058", "#59636b"))
        view.set_ink_only(False)
        self.assertEqual(view.viewport().grab().toImage(), composite)

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

    def test_overlay_watermark_draws_over_ink_and_markers_at_top(self):
        overlay = RouteOverlay(ink_opacity=0)
        self.addCleanup(overlay.close)
        overlay.resize(200, 200)
        overlay.pixel_size = (200, 200)
        overlay.matrix = QTransform()
        ink = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
        ink.fill(QColor("red"))
        overlay.project = Project(np.zeros((200, 200, 3), np.uint8), ink=ink)

        frame = overlay.grab().toImage()

        def white_contribution(x, y):
            color = frame.pixelColor(x, y)
            return color.green() * color.alpha()

        x, y = max(((x, y) for x in range(70, 130) for y in range(40, 53)),
                   key=lambda point: white_contribution(*point))
        self.assertGreater(white_contribution(x, y), 0)
        self.assertLess(frame.pixelColor(x, y).alpha(), 160)

        overlay.ink_opacity = 1
        over_ink = overlay.grab().toImage().pixelColor(x, y)
        self.assertEqual(over_ink.alpha(), 255)
        self.assertGreater(over_ink.green(), 0)

        icon = QImage(8, 8, ink.format())
        icon.fill(QColor("blue"))
        overlay.project.add_marker(Marker(decode_asset(encode_ink(icon)), 100, 45, 70))
        over_marker = overlay.grab().toImage().pixelColor(x, y)
        self.assertGreater(over_marker.blue(), over_marker.green())
        self.assertGreater(over_marker.green(), 0)

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
