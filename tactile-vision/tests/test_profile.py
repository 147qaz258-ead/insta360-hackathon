import unittest

from compiler.profiles import DeviceProfile


class DeviceProfileTests(unittest.TestCase):
    def test_800x480_target_3840_resolves_to_80x48(self):
        profile = DeviceProfile(
            name="test",
            screen_px_width=800,
            screen_px_height=480,
            target_pin_count=3840,
            grid_align=2,
        )
        self.assertEqual(profile.compute_grid(), (80, 48))


if __name__ == "__main__":
    unittest.main()
