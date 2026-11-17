import unittest
from unittest.mock import patch

import numpy as np
from map_overlay.capture import capture_minimap, PreviewWorker
from map_overlay.settings import MinimapPosition


class PreviewCaptureTests(unittest.TestCase):
    def setUp(self):
        self.position = MinimapPosition([20, 30, 100, 100], [640, 480])
        self.box = (100, 100, 640, 480)
        self.image = np.zeros((100, 100, 3), dtype=np.uint8)

    def test_capture_uses_relative_position_and_rejects_focus_move_resize(self):
        with patch("map_overlay.capture.native.client_box", return_value=self.box) as client, \
                patch("map_overlay.capture.native.foreground_is", return_value=True) as foreground, \
                patch("map_overlay.capture.native.monitor_for"), \
                patch("map_overlay.capture.grab", return_value=self.image) as grab:
            payload = capture_minimap(123, self.position, "session")
            self.assertIs(payload["image"], self.image)
            grab.assert_called_once_with((120, 130, 100, 100), "session")
            for box, focus in [(None, True), ((100, 100, 800, 600), True), (self.box, False)]:
                client.return_value, foreground.return_value = box, focus
                grab.reset_mock()
                self.assertIsNone(capture_minimap(123, self.position, "session")["image"])
                grab.assert_not_called()
            client.return_value = self.box
            foreground.side_effect = [True, False]
            self.assertIsNone(capture_minimap(123, self.position, "session")["image"])
            foreground.side_effect = None
            foreground.return_value = True
            client.side_effect = [self.box, (120, 100, 640, 480)]
            self.assertIsNone(capture_minimap(123, self.position, "session")["image"])

    def test_preview_worker_does_not_construct_localizer(self):
        # Run one capture cycle synchronously; stopping from the signal avoids sleeps.
        worker = PreviewWorker(123, self.position, 7)
        received = []
        worker.result.connect(lambda payload: (received.append(payload), worker.stop()))
        with patch("map_overlay.capture.mss.mss"), \
                patch("map_overlay.capture.capture_minimap", return_value=dict(image=self.image)) as capture, \
                patch("map_overlay.capture.Localizer") as localizer:
            worker.run()
        localizer.assert_not_called()
        capture.assert_called_once()
        self.assertEqual(received[0]["generation"], 7)
        self.assertGreaterEqual(worker.interval, 1 / 3)


if __name__ == "__main__":
    unittest.main()
