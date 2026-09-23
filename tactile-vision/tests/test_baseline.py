import unittest

from compiler.baseline import BaselineConfig, extract_structural_map


class BaselineTests(unittest.TestCase):
    def test_sobel_baseline_finds_contrast_boundary(self):
        grayscale = [
            [0, 0, 0, 0, 255, 255, 255, 255, 255]
            for _ in range(9)
        ]
        structural = extract_structural_map(
            grayscale,
            BaselineConfig(
                edge_quantile=0.5,
                minimum_gradient=10,
                close_iterations=0,
                minimum_component_pixels=1,
            ),
        )
        active_columns = {
            x
            for y, row in enumerate(structural)
            for x, value in enumerate(row)
            if value
        }
        self.assertTrue(active_columns)
        self.assertTrue(active_columns.issubset({3, 4}))

    def test_uniform_image_has_no_structure(self):
        grayscale = [[100 for _ in range(7)] for _ in range(7)]
        structural = extract_structural_map(grayscale)
        self.assertEqual(sum(map(sum, structural)), 0)


if __name__ == "__main__":
    unittest.main()
