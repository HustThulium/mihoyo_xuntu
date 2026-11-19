import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from map_overlay.annotations import AnnotationEdits
from map_overlay.project import INK_FORMAT, Project
from map_overlay.route_import import RouteExtraction
from map_overlay.route_import_ui import RouteMatchDialog, RoutePreviewDialog
from map_overlay.vision import Localizer, Match


class RouteImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.base = np.full((140, 180, 3), 100, np.uint8)
        source = self.base[20:110, 30:160].copy()
        source[40:45, 10:120] = (210, 30, 20)
        source[20, 20] = 150
        self.extraction = RouteExtraction(self.base, source, np.float64([[1, 0, 30], [0, 1, 20]]))

    def test_registration_crop_does_not_change_tracker_and_rejects_blank(self):
        rng = np.random.default_rng(2)
        ref = rng.integers(0, 256, (480, 600, 3), np.uint8)
        localizer = Localizer(ref)
        query = cv2.resize(ref[80:400, 100:450], None, fx=1.2, fy=1.2)
        match = localizer.register_route(query)
        np.testing.assert_allclose(match.matrix, [[1/1.2, 0, 100], [0, 1/1.2, 80]], atol=.5)
        self.assertEqual(localizer.frames, 0)
        self.assertIsNone(localizer.previous)
        with self.assertRaises(ValueError):
            localizer.register_route(np.zeros_like(query))

    def test_inset_in_an_unrelated_image_does_not_extend_to_full_image(self):
        rng = np.random.default_rng(7)
        reference = rng.integers(0, 256, (420, 550, 3), np.uint8)
        collage = np.full((600, 920, 3), 80, np.uint8)
        collage[40:200, 310:510] = reference[160:320, 70:270]
        match = Localizer(reference).register_route(collage)
        self.assertIsNotNone(match.route_clip)
        x, y, w, h = cv2.boundingRect(match.route_clip)
        self.assertTrue(308 <= x < x+w <= 512 and 38 <= y < y+h <= 202)
        np.testing.assert_allclose(match.matrix, [[1, 0, -240], [0, 1, 120]], atol=1)

    def test_cleanup_sensitivity_and_outside_source(self):
        raw = self.extraction.image(65)
        cleaned = self.extraction.image(65, clean=True)
        self.assertGreater(raw.pixelColor(20, 20).alpha(), 0)
        self.assertEqual(cleaned.pixelColor(20, 20).alpha(), 0)
        self.assertEqual(cleaned.pixelColor(40, 42), raw.pixelColor(40, 42))
        self.assertGreater(raw.pixelColor(40, 42).alpha(), 0)
        self.assertEqual(self.extraction.image(0).pixelColor(20, 20).alpha(), 0)
        self.assertEqual(raw.pixelColor(raw.width()-1, raw.height()-1).alpha(), 0)

    def test_match_confirmation_and_partial_extraction_share_one_outline(self):
        source = self.base.copy()
        source[35:90, 30:90] = (230, 12, 20)
        landmarks = np.float32([(30, 30), (90, 30), (30, 90), (45, 45)])
        match = Match(matrix=np.eye(2, 3), coverage=.08, inliers=4, route_landmarks=landmarks)
        confirmation = RouteMatchDialog(source, self.base, match)
        self.addCleanup(confirmation.close)
        self.assertTrue(confirmation.next.isEnabled())
        self.assertIn("仅局部匹配", confirmation.hint.text())
        extraction = RouteExtraction(self.base, source, match.matrix, match.route_clip)
        ink = extraction.image(65)
        self.assertEqual(extraction.origin, (30, 30))
        self.assertGreater(ink.pixelColor(15, 15).alpha(), 0)
        self.assertEqual(ink.pixelColor(49, 49).alpha(), 0)
        failed = RouteMatchDialog(source, self.base, None)
        self.addCleanup(failed.close)
        self.assertFalse(failed.next.isEnabled())
        failed.choose_area()
        self.assertTrue(failed.reselect)

    def test_full_map_preview_shows_old_ink_outside_import_and_keeps_it_separate(self):
        old_ink = QImage(180, 140, INK_FORMAT)
        old_ink.fill(Qt.GlobalColor.transparent)
        old_ink.setPixelColor(10, 10, QColor("yellow"))
        project = Project(self.base, ink=old_ink)
        dialog = RoutePreviewDialog(self.extraction, project)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        view = dialog.view
        self.assertEqual(view.sceneRect().size().toSize(), old_ink.size())
        view.resetTransform()
        def pixel(x, y):
            self.app.processEvents()
            point = view.mapFromScene(x, y)
            return view.viewport().grab().toImage().pixelColor(point)
        self.assertEqual(pixel(10, 10), QColor("yellow"))
        self.assertEqual(pixel(70, 62), view.ink.pixelColor(40, 42))
        dialog.ink_mode.click()
        self.assertNotEqual(pixel(10, 10), QColor("yellow"))
        self.assertEqual(pixel(70, 62), view.ink.pixelColor(40, 42))
        dialog.advance()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(10, 10))
        dialog.composite_mode.click()
        self.assertEqual(pixel(10, 10), QColor("yellow"))
        self.assertEqual(project.ink.pixelColor(10, 10), QColor("yellow"))

    def test_preview_undo_and_save_restore_existing_ink(self):
        dialog = RoutePreviewDialog(self.extraction, Project(self.base))
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        dialog.advance()
        dialog.clean()
        view = dialog.view
        full_scale = view.transform().m11()
        self.assertTrue(dialog.full_scope.isChecked() and dialog.composite_mode.isChecked())
        dialog.match_scope.click()
        self.assertTrue(dialog.match_scope.isChecked() and dialog.composite_mode.isChecked())
        self.assertGreater(view.transform().m11(), full_scale)
        dialog.ink_mode.click()
        self.assertFalse(dialog.composite_mode.isChecked())
        self.assertTrue(view.ink_only)
        self.assertTrue(dialog.match_scope.isChecked())
        dialog.full_scope.click()
        self.assertAlmostEqual(view.transform().m11(), full_scale, places=1)
        self.assertTrue(dialog.ink_mode.isChecked())
        dialog.size.setValue(8)
        start = view.mapFromScene(70, 57)
        end = view.mapFromScene(70, 68)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(view.ink.pixelColor(40, 42).alpha(), 0)
        dialog.previous()
        dialog.slider.setValue(75)
        dialog.advance()
        self.assertEqual(view.sensitivity, 75)
        self.assertEqual(view.ink.pixelColor(40, 42).alpha(), 0)
        dialog.undo()
        self.assertGreater(view.ink.pixelColor(40, 42).alpha(), 0)
        dialog.undo()
        self.assertFalse(view.cleaned)
        dialog.advance()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

        original = QImage(180, 140, INK_FORMAT)
        original.fill(QColor("#abcdef"))
        project = Project(self.base, ink=original.copy())
        edits = AnnotationEdits(project)
        edits.merge_ink(self.extraction.origin, view.ink)
        self.assertNotEqual(project.ink.pixelColor(70, 62), original.pixelColor(70, 62))
        self.assertEqual(project.ink.pixelColor(30, 20), original.pixelColor(30, 20))
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "import.routepack"
            project.save(file)
            self.assertEqual(Project.load(file).ink, project.ink)
        self.assertTrue(edits.undo())
        self.assertEqual(project.ink, original)
        self.assertFalse(edits.undo())

    def test_empty_extraction_cannot_be_saved(self):
        unchanged = RouteExtraction(self.base, self.base, np.eye(2, 3))
        dialog = RoutePreviewDialog(unchanged, Project(self.base))
        self.addCleanup(dialog.close)
        dialog.advance()
        dialog.advance()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("没有可保存", dialog.message.text())
