import unittest

from tactile.sparse_runs import (
    compute_content_rect,
    compute_sparse_metrics,
    expand_sparse_candidate,
    validate_sparse_candidate,
)


RECT_FULL = {"x": 0, "y": 0, "w": 80, "h": 48}
RECT_LETTERBOX = {"x": 8, "y": 0, "w": 64, "h": 48}


def make_candidate(runs, content_rect=RECT_FULL, regions=None):
    return {
        "version": "sparse-runs-v1",
        "device_id": "rdk_hdmi",
        "cols": 80,
        "rows": 48,
        "height_encoding": "uint8",
        "content_rect": dict(content_rect),
        "regions": regions or [
            {"id": 1, "name": "主体", "description": "主体轮廓", "speech": "这里是主体。"},
            {"id": 2, "name": "桌面", "description": "桌面边界", "speech": "这里是桌面。"},
        ],
        "raised_runs": runs,
        "scene_summary": "测试场景",
        "audio_overview": "测试语音概览",
        "design_notes": "测试设计说明",
    }


def sample_runs():
    return [
        {"row": 10, "start_col": 20, "end_col": 23, "height": 200, "region_id": 1},
        {"row": 11, "start_col": 20, "end_col": 20, "height": 180, "region_id": 1},
        {"row": 30, "start_col": 5, "end_col": 70, "height": 90, "region_id": 2},
    ]


class ContentRectTests(unittest.TestCase):
    def test_exact_grid_ratio_fills_whole_surface(self):
        self.assertEqual(compute_content_rect(1600, 960), RECT_FULL)
        self.assertEqual(compute_content_rect(80, 48), RECT_FULL)

    def test_wider_image_letterboxes_top_and_bottom(self):
        rect = compute_content_rect(1600, 900)  # 16:9
        self.assertEqual(rect["w"], 80)
        self.assertLess(rect["h"], 48)
        self.assertGreaterEqual(rect["y"], 1)
        self.assertEqual(rect["x"], 0)

    def test_narrower_image_pillars_left_and_right(self):
        rect = compute_content_rect(800, 480 * 2)  # portrait
        self.assertEqual(rect["h"], 48)
        self.assertLess(rect["w"], 80)
        self.assertGreaterEqual(rect["x"], 1)
        self.assertEqual(rect["y"], 0)

    def test_four_three_image(self):
        rect = compute_content_rect(1024, 768)  # 4:3
        self.assertEqual(rect, {"x": 8, "y": 0, "w": 64, "h": 48})

    def test_invalid_size_rejected(self):
        with self.assertRaises(ValueError):
            compute_content_rect(0, 100)


class SparseValidationTests(unittest.TestCase):
    def test_valid_candidate_passes(self):
        report = validate_sparse_candidate(make_candidate(sample_runs()), RECT_FULL)
        self.assertTrue(report.valid, [e.to_dict() for e in report.errors])

    def test_expands_to_exactly_3840_lossless_values(self):
        candidate = make_candidate(sample_runs())
        report = validate_sparse_candidate(candidate, RECT_FULL)
        self.assertTrue(report.valid)
        expanded = expand_sparse_candidate(candidate)
        heights = expanded["height_rows"]
        regions = expanded["region_rows"]
        self.assertEqual(len(heights), 48)
        self.assertTrue(all(len(row) == 80 for row in heights))
        flat_h = [v for row in heights for v in row]
        flat_r = [v for row in regions for v in row]
        self.assertEqual(len(flat_h), 3840)
        self.assertEqual(len(flat_r), 3840)
        # 点段覆盖处精确等于模型值
        self.assertEqual(heights[10][20], 200)
        self.assertEqual(heights[10][23], 200)
        self.assertEqual(regions[10][21], 1)
        self.assertEqual(heights[11][20], 180)
        self.assertEqual(heights[30][5], 90)
        self.assertEqual(heights[30][70], 90)
        self.assertEqual(regions[30][40], 2)
        # 未覆盖处机械为 0
        self.assertEqual(heights[0][0], 0)
        self.assertEqual(regions[47][79], 0)
        self.assertEqual(heights[10][24], 0)

    def test_out_of_bounds_reported_precisely(self):
        candidate = make_candidate([
            {"row": 48, "start_col": 0, "end_col": 1, "height": 100, "region_id": 1},
            {"row": 3, "start_col": 79, "end_col": 80, "height": 100, "region_id": 1},
        ])
        report = validate_sparse_candidate(candidate, RECT_FULL)
        self.assertFalse(report.valid)
        paths = [e.path for e in report.errors]
        self.assertIn("raised_runs[0].row", paths)
        self.assertIn("raised_runs[1].end_col", paths)
        codes = {e.code for e in report.errors}
        self.assertIn("out_of_bounds", codes)

    def test_overlap_reported_with_exact_segments(self):
        candidate = make_candidate([
            {"row": 5, "start_col": 10, "end_col": 20, "height": 100, "region_id": 1},
            {"row": 5, "start_col": 18, "end_col": 30, "height": 120, "region_id": 1},
        ])
        report = validate_sparse_candidate(candidate, RECT_FULL)
        self.assertFalse(report.valid)
        overlaps = [e for e in report.errors if e.code == "overlap"]
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0].path, "raised_runs[1]")
        self.assertIn("重叠", overlaps[0].message or overlaps[0].actual)

    def test_unknown_region_reported(self):
        candidate = make_candidate([
            {"row": 1, "start_col": 1, "end_col": 2, "height": 100, "region_id": 9},
        ])
        report = validate_sparse_candidate(candidate, RECT_FULL)
        self.assertFalse(report.valid)
        self.assertEqual(report.errors[0].code, "unknown_region")

    def test_illegal_heights_reported(self):
        for bad in (0, 256, -5, "high", True):
            candidate = make_candidate([
                {"row": 1, "start_col": 1, "end_col": 2, "height": bad, "region_id": 1},
            ])
            report = validate_sparse_candidate(candidate, RECT_FULL)
            self.assertFalse(report.valid, f"height={bad!r} must fail")
            self.assertIn(report.errors[0].code, {"illegal_height", "integer"})

    def test_inverted_range_reported(self):
        candidate = make_candidate([
            {"row": 1, "start_col": 10, "end_col": 4, "height": 100, "region_id": 1},
        ])
        report = validate_sparse_candidate(candidate, RECT_FULL)
        self.assertFalse(report.valid)
        self.assertEqual(report.errors[0].code, "inverted_range")

    def test_runs_outside_content_rect_rejected(self):
        candidate = make_candidate(
            [{"row": 0, "start_col": 2, "end_col": 6, "height": 100, "region_id": 1}],
            content_rect=RECT_LETTERBOX,
        )
        report = validate_sparse_candidate(candidate, RECT_LETTERBOX)
        self.assertFalse(report.valid)
        self.assertEqual(report.errors[0].code, "outside_content_rect")

    def test_content_rect_mismatch_rejected(self):
        candidate = make_candidate(sample_runs(), content_rect=RECT_FULL)
        report = validate_sparse_candidate(candidate, RECT_LETTERBOX)
        self.assertFalse(report.valid)
        self.assertTrue(any(e.code == "content_rect" for e in report.errors))

    def test_wrong_protocol_version_rejected(self):
        candidate = make_candidate(sample_runs())
        candidate["version"] = 2
        report = validate_sparse_candidate(candidate, RECT_FULL)
        self.assertFalse(report.valid)
        self.assertTrue(any(e.path == "version" for e in report.errors))

    def test_metrics_are_mechanical_facts(self):
        candidate = make_candidate(sample_runs())
        expanded = expand_sparse_candidate(candidate)
        metrics = compute_sparse_metrics(
            expanded["height_rows"], expanded["region_rows"], candidate["regions"], RECT_FULL
        )
        self.assertEqual(metrics["pin_count"], 3840)
        self.assertEqual(metrics["active_pins"], 4 + 1 + 66)
        self.assertEqual(metrics["longest_horizontal_run"], 66)
        self.assertEqual(metrics["height_stats"]["max"], 200)
        self.assertEqual(metrics["height_stats"]["distinct_levels"], 3)
        self.assertIn("1:主体", metrics["region_pin_counts"])
        self.assertEqual(metrics["region_pin_counts"]["2:桌面"], 66)


if __name__ == "__main__":
    unittest.main()
