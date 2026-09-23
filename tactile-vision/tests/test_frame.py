import unittest

from tactile.frame import TactileFrame


class TactileFrameTests(unittest.TestCase):
    def test_frame_validates_pin_count(self):
        with self.assertRaises(ValueError):
            TactileFrame(
                version=1,
                cols=2,
                rows=2,
                levels=2,
                aspect_ratio=1.0,
                pins=[0, 1, 0],
            )

    def test_from_rows(self):
        frame = TactileFrame.from_rows([[0, 1], [1, 0]])
        self.assertEqual(frame.pins, [0, 1, 1, 0])
        self.assertEqual(frame.at(1, 0), 1)


if __name__ == "__main__":
    unittest.main()
