import unittest

from compiler.profiles import DeviceProfile
from compiler.scene_compiler import TactileSceneCompiler
from scene.tactile_scene import TactileScene


def make_profile(**overrides):
    data = {
        "name": "test_hdmi",
        "screen_px_width": 800,
        "screen_px_height": 480,
        "target_pin_count": 3840,
        "height_levels": 4,
    }
    data.update(overrides)
    return DeviceProfile.from_dict(data)


def make_scene(regions, image_width=1600, image_height=900):
    return TactileScene.from_dict({
        "content_type": "photo",
        "image_width": image_width,
        "image_height": image_height,
        "regions": regions,
    })


def full_frame_scene():
    # One region covering the whole image at level 3.
    return make_scene([
        {
            "id": "frame",
            "boundary_level": 3,
            "contours": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]],
        }
    ])


class TactileSceneCompilerTests(unittest.TestCase):
    def setUp(self):
        self.compiler = TactileSceneCompiler()
        self.profile = make_profile()

    def compile(self, scene, profile=None):
        profile = profile or self.profile
        return self.compiler.compile(scene, profile)

    def test_content_box_preserves_aspect_without_crop(self):
        frame = self.compile(full_frame_scene())
        box = frame.content_box
        self.assertEqual(box["x"] + box["width"], 80)
        self.assertGreater(box["height"], 0)
        self.assertLess(box["height"], frame.rows)  # 16:9 into 5:3 grid pads vertically
        self.assertEqual(box["width"], 80)
        # padding split evenly top/bottom
        self.assertEqual(box["y"], (frame.rows - box["height"]) // 2)

    def test_left_right_positions_preserved(self):
        scene = make_scene([
            {
                "id": "left_obj",
                "boundary_level": 3,
                "contours": [[[0.05, 0.3], [0.4, 0.3], [0.4, 0.7], [0.05, 0.7]]],
            },
            {
                "id": "right_obj",
                "boundary_level": 2,
                "contours": [[[0.6, 0.3], [0.95, 0.3], [0.95, 0.7], [0.6, 0.7]]],
            },
        ])
        frame = self.compile(scene)
        cols = frame.cols
        left_cells = [i for i, pin in enumerate(frame.pins) if pin == 3]
        right_cells = [i for i, pin in enumerate(frame.pins) if pin == 2]
        self.assertTrue(left_cells)
        self.assertTrue(right_cells)
        left_mean_x = sum(i % cols for i in left_cells) / len(left_cells)
        right_mean_x = sum(i % cols for i in right_cells) / len(right_cells)
        self.assertLess(left_mean_x, right_mean_x)
        hit_map = self.compiler.region_hit_map()
        self.assertIn("left_obj", hit_map)
        self.assertIn("right_obj", hit_map)

    def test_top_bottom_positions_preserved(self):
        scene = make_scene([
            {
                "id": "top_obj",
                "boundary_level": 3,
                "contours": [[[0.3, 0.05], [0.7, 0.05], [0.7, 0.35], [0.3, 0.35]]],
            },
            {
                "id": "bottom_obj",
                "boundary_level": 2,
                "contours": [[[0.3, 0.6], [0.7, 0.6], [0.7, 0.95], [0.3, 0.95]]],
            },
        ])
        frame = self.compile(scene)
        cols = frame.cols
        top_cells = [i for i, pin in enumerate(frame.pins) if pin == 3]
        bottom_cells = [i for i, pin in enumerate(frame.pins) if pin == 2]
        top_mean_y = sum(i // cols for i in top_cells) / len(top_cells)
        bottom_mean_y = sum(i // cols for i in bottom_cells) / len(bottom_cells)
        self.assertLess(top_mean_y, bottom_mean_y)

    def test_solid_fill_interior(self):
        scene = make_scene([
            {
                "id": "solid_box",
                "boundary_level": 3,
                "internal_level": 1,
                "fill_mode": "solid",
                "contours": [[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]],
            }
        ])
        frame = self.compile(scene)
        level1 = sum(1 for pin in frame.pins if pin == 1)
        level3 = sum(1 for pin in frame.pins if pin == 3)
        self.assertGreater(level1, 50)  # interior filled
        self.assertGreater(level3, 20)  # outline present

    def test_sparse_sparser_than_solid(self):
        region = {
            "id": "box",
            "boundary_level": 3,
            "internal_level": 1,
            "contours": [[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]],
        }
        solid = self.compile(make_scene([{**region, "fill_mode": "solid"}]))
        solid_count = sum(1 for pin in solid.pins if pin == 1)
        sparse = self.compile(make_scene([{**region, "fill_mode": "sparse"}]))
        sparse_count = sum(1 for pin in sparse.pins if pin == 1)
        self.assertGreater(solid_count, sparse_count * 1.5)

    def test_levels_clamped_to_device(self):
        binary_profile = make_profile(name="binary", height_levels=2)
        scene = make_scene([
            {
                "id": "obj",
                "boundary_level": 3,
                "internal_level": 2,
                "fill_mode": "solid",
                "contours": [[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]],
            }
        ])
        frame = self.compile(scene, binary_profile)
        self.assertTrue(all(pin in (0, 1) for pin in frame.pins))
        self.assertEqual(frame.levels, 2)

    def test_deterministic_output(self):
        scene = full_frame_scene()
        frame_a = self.compile(scene)
        frame_b = TactileSceneCompiler().compile(scene, self.profile)
        self.assertEqual(frame_a.pins, frame_b.pins)

    def test_degenerate_region_still_visible(self):
        scene = make_scene([
            {
                "id": "tiny",
                "boundary_level": 3,
                "contours": [[[0.5, 0.5], [0.5, 0.5]]],
            }
        ])
        frame = self.compile(scene)
        self.assertEqual(sum(1 for pin in frame.pins if pin > 0), 1)

    def test_cross_device_layout_consistency(self):
        scene = make_scene([
            {
                "id": "left_obj",
                "boundary_level": 3,
                "contours": [[[0.05, 0.3], [0.4, 0.3], [0.4, 0.7], [0.05, 0.7]]],
            },
            {
                "id": "right_obj",
                "boundary_level": 2,
                "contours": [[[0.6, 0.3], [0.95, 0.3], [0.95, 0.7], [0.6, 0.7]]],
            },
        ])
        profile_a = make_profile()
        profile_b = make_profile(name="other", target_pin_count=2400, grid_align=2)
        frame_a = self.compile(scene, profile_a)
        frame_b = self.compile(scene, profile_b)
        for frame in (frame_a, frame_b):
            cols = frame.cols
            left = [i % cols for i, pin in enumerate(frame.pins) if pin == 3]
            right = [i % cols for i, pin in enumerate(frame.pins) if pin == 2]
            self.assertLess(sum(left) / len(left), sum(right) / len(right))


if __name__ == "__main__":
    unittest.main()
