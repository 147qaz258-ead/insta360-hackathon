import unittest

from scene.tactile_scene import TactileRegion, TactileScene


class TactileSceneTests(unittest.TestCase):
    def test_region_round_trip_and_clamps(self):
        region = TactileRegion.from_dict({
            "id": "person_left",
            "name": "person",
            "tactile_role": "primary",
            "boundary_level": 9,
            "internal_level": -2,
            "fill_mode": "SOLID",
            "contours": [[[0, 0], [1.5, 0.5], [0.5, 2], "bad", [0.1]]],
            "source": "weird",
        })
        self.assertEqual(region.boundary_level, 3)
        self.assertEqual(region.internal_level, 0)
        self.assertEqual(region.fill_mode, "solid")
        self.assertEqual(region.source, "approximate")
        # invalid points are dropped, out-of-range coords clamped
        self.assertEqual(region.contours, [[[0.0, 0.0], [1.0, 0.5], [0.5, 1.0]]])

        restored = TactileRegion.from_dict(region.to_dict())
        self.assertEqual(restored, region)

    def test_scene_round_trip(self):
        scene = TactileScene.from_dict({
            "content_type": "photo",
            "global_strategy": "outline first",
            "image_width": 1600,
            "image_height": 900,
            "regions": [{
                "id": "table",
                "name": "table",
                "boundary_level": 1,
                "contours": [[[0.0, 0.6], [1.0, 0.6], [1.0, 0.8], [0.0, 0.8]]],
                "source": "approximate",
            }],
            "relations": ["table below person_left"],
            "warnings": ["w"],
            "metrics": {"planned_regions": 1},
        })
        self.assertEqual(scene.image_width, 1600)
        self.assertEqual(len(scene.regions), 1)
        restored = TactileScene.from_dict(scene.to_dict())
        self.assertEqual(restored, scene)

    def test_empty_contours_dropped(self):
        region = TactileRegion.from_dict({
            "id": "x",
            "contours": [[], [[0.0, 0.0]]],
        })
        # contours need >= 2 points to be usable
        self.assertEqual(region.contours, [])


if __name__ == "__main__":
    unittest.main()
