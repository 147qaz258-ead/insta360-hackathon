import unittest

from tactile.synthetic import PATTERNS, make_pattern


class SyntheticTests(unittest.TestCase):
    def test_all_patterns_are_rectangular_and_nonempty(self):
        for name in PATTERNS:
            grid = make_pattern(name)
            self.assertTrue(grid)
            width = len(grid[0])
            self.assertTrue(all(len(row) == width for row in grid))
            self.assertGreater(sum(sum(row) for row in grid), 0)


if __name__ == "__main__":
    unittest.main()
