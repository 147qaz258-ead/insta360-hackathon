import unittest

from compiler.profiles import DeviceProfile
from tactile.rasterizer import AdaptiveRasterizer


class RasterizerTests(unittest.TestCase):
    def setUp(self):
        self.profile = DeviceProfile(
            name="test",
            screen_px_width=800,
            screen_px_height=480,
            target_pin_count=3840,
            grid_align=2,
        )
        self.rasterizer = AdaptiveRasterizer()

    def test_preserves_aspect_with_padding_instead_of_stretch(self):
        source = [[1 for _ in range(4)] for _ in range(2)]  # 2:1
        frame = self.rasterizer.rasterize(source, self.profile)
        self.assertEqual((frame.cols, frame.rows), (80, 48))
        self.assertEqual(frame.content_box["width"], 80)
        self.assertEqual(frame.content_box["height"], 40)
        self.assertEqual(frame.content_box["y"], 4)

    def test_left_and_right_structure_remain_left_and_right(self):
        source = [
            [1, 0, 0, 1],
            [1, 0, 0, 1],
        ]
        frame = self.rasterizer.rasterize(source, self.profile)
        box = frame.content_box
        y = box["y"] + box["height"] // 2
        self.assertEqual(frame.at(box["x"], y), 1)
        self.assertEqual(frame.at(box["x"] + box["width"] - 1, y), 1)

    def test_source_is_not_cropped(self):
        source = [
            [1, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 1],
        ]
        frame = self.rasterizer.rasterize(source, self.profile)
        self.assertGreater(sum(frame.pins), 0)
        self.assertEqual(frame.source_size, {"width": 4, "height": 3})

    def test_downsample_preserves_thin_line_outside_nearest_sample_centres(self):
        profile = DeviceProfile(
            name="tiny",
            screen_px_width=4,
            screen_px_height=1,
            target_pin_count=4,
            grid_align=1,
        )
        source = [
            [1, 0, 0, 0, 0, 0, 0, 0],
            [1, 0, 0, 0, 0, 0, 0, 0],
        ]
        frame = self.rasterizer.rasterize(source, profile)
        self.assertEqual((frame.cols, frame.rows), (4, 1))
        self.assertEqual(frame.at(0, 0), 1)


if __name__ == "__main__":
    unittest.main()
