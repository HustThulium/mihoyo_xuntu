from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import cv2
import numpy as np

from map_overlay.demo import make_demo, mini_at
from map_overlay.geometry import inverse, similarity, transform_points
from map_overlay.project import Project
from map_overlay.vision import Localizer, Match, PoseGate


class GeometryTests(unittest.TestCase):
    def test_user_landmarks_recover_scale_and_translation(self):
        source = np.array([[10, 20], [180, 25], [30, 170], [160, 160]], float)
        expected = np.array([[2., 0., 350.], [0., 2., 190.]])
        destination = transform_points(source, expected)
        recovered, error = similarity(source, destination)
        np.testing.assert_allclose(recovered, expected, atol=1e-8)
        np.testing.assert_allclose(transform_points(destination, inverse(recovered)), source, atol=1e-8)
        self.assertLess(error, 1e-8)

    def test_degenerate_landmarks_are_rejected(self):
        with self.assertRaises(ValueError):
            similarity([[1, 1], [2, 2], [3, 3]], [[4, 4], [5, 5], [6, 6]])


class VisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv2.setNumThreads(2)
        cls.project = make_demo()
        cls.localizer = Localizer(cls.project.image)

    def test_zoom_levels_occlusion_and_movement(self):
        for scale in (.5, .85, 1.4):
            self.localizer.reset()
            for center in [(560, 485), (580, 478), (600, 465)]:
                with self.subTest(scale=scale, center=center):
                    image, expected = mini_at(self.project.image, center, scale)
                    # Simulate minimap brightness changes while retaining geometry.
                    image = cv2.convertScaleAbs(image, alpha=.91, beta=8)
                    found = self.localizer.locate(image)
                    self.assertTrue(found.ok, repr(found))
                    landmarks = [[85, 90], [195, 180], [140, 140]]
                    errors = np.linalg.norm(transform_points(landmarks, found.matrix) -
                                            transform_points(landmarks, expected), axis=1)
                    self.assertLess(float(errors.max()), 2.5)

    def test_blank_frame_does_not_reuse_previous_pose(self):
        image, _ = mini_at(self.project.image)
        self.assertTrue(self.localizer.locate(image).ok)
        self.assertFalse(self.localizer.locate(np.zeros_like(image)).ok)

    def test_rotating_minimap_is_out_of_scope(self):
        image, _ = mini_at(self.project.image, angle=25)
        self.localizer.reset()
        self.assertFalse(self.localizer.locate(image).ok)

    def test_reacquisition_and_jump_need_two_frames(self):
        gate = PoseGate()
        image, matrix = mini_at(self.project.image)
        first = Match(matrix=matrix)
        self.assertFalse(gate.accept(first, image.shape))
        self.assertTrue(gate.accept(first, image.shape))
        gate.accept(Match(), image.shape)
        self.assertFalse(gate.accept(first, image.shape))
        self.assertTrue(gate.accept(first, image.shape))
        _, far = mini_at(self.project.image, center=(900, 200))
        self.assertFalse(gate.accept(Match(matrix=far), image.shape))
        self.assertTrue(gate.accept(Match(matrix=far), image.shape))


class PersistenceTests(unittest.TestCase):
    def test_unicode_path_roundtrip_and_tampered_coordinates(self):
        project = make_demo()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "区域路线.routepack"
            project.save(path)
            loaded = Project.load(path)
            np.testing.assert_array_equal(loaded.image, project.image)
            self.assertEqual(loaded.ink, project.ink)
            self.assertEqual([(m.x, m.y, m.size) for m in loaded.markers],
                             [(m.x, m.y, m.size) for m in project.markers])
            self.assertIs(loaded.markers[0].asset, loaded.markers[1].asset)
            self.assertEqual(loaded.markers[0].asset.png, project.markers[0].asset.png)
            with zipfile.ZipFile(path) as file:
                self.assertEqual(sum(name.startswith("assets/") for name in file.namelist()), 1)
                entries = {name: file.read(name) for name in file.namelist()}
            payload = json.loads(entries["project.json"])
            payload["markers"][0]["x"] = float("nan")
            entries["project.json"] = json.dumps(payload)
            with zipfile.ZipFile(path, "w") as file:
                for name, content in entries.items():
                    file.writestr(name, content)
            with self.assertRaisesRegex(ValueError, "标记坐标"):
                Project.load(path)

    def test_device_fields_in_region_bundle_are_ignored_and_not_exported(self):
        project = Project(make_demo().image)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.routepack"
            project.save(path)
            with zipfile.ZipFile(path) as bundle:
                payload = json.loads(bundle.read("project.json"))
                image = bundle.read("reference.png")
            payload.update(mini_rect=[1000, 1000, 300, 300], client_size=[1100, 1100])
            with zipfile.ZipFile(path, "w") as bundle:
                bundle.writestr("project.json", json.dumps(payload))
                bundle.writestr("reference.png", image)
            loaded = Project.load(path)
            self.assertFalse(hasattr(loaded, "legacy_minimap"))
            self.assertFalse(loaded.has_annotations)
            loaded.save(path)
            with zipfile.ZipFile(path) as bundle:
                saved = json.loads(bundle.read("project.json"))
            self.assertNotIn("mini_rect", saved)
            self.assertNotIn("client_size", saved)


if __name__ == "__main__":
    unittest.main()
