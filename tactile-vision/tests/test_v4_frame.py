import copy
import unittest

from tactile.preview import render_preview_png
from tactile.v4_frame import publish_tactile_frame, validate_tactile_candidate


def valid_candidate():
    heights = [[0] * 80 for _ in range(48)]
    regions = [[0] * 80 for _ in range(48)]
    heights[10][20] = 173
    regions[10][20] = 1
    return {
        "version": 2,
        "device_id": "rdk_hdmi",
        "cols": 80,
        "rows": 48,
        "height_encoding": "uint8",
        "height_rows": heights,
        "region_rows": regions,
        "regions": [{"id": 1, "name": "主体", "description": "主体轮廓", "speech": "这里是主体。"}],
        "scene_summary": "测试图片",
        "audio_overview": "图片中间有一个主体。",
        "self_review": {"protocol_check": "complete"},
    }


class V4FrameTests(unittest.TestCase):
    def test_valid_candidate_publishes_exact_uint8_frame(self):
        candidate = valid_candidate()
        frame = publish_tactile_frame(candidate, 7)
        self.assertEqual((frame.cols, frame.rows, frame.levels), (80, 48, 256))
        self.assertEqual(len(frame.pins), 3840)
        self.assertEqual(frame.pins[10 * 80 + 20], 173)
        self.assertEqual(frame.region_at(10, 20)["id"], 1)
        self.assertTrue(frame.checksum.startswith("sha256:"))

    def test_validator_reports_exact_row_and_column_without_repair(self):
        candidate = valid_candidate()
        candidate["height_rows"][3] = candidate["height_rows"][3][:-1]
        candidate["height_rows"][4][9] = 300
        original = copy.deepcopy(candidate)
        report = validate_tactile_candidate(candidate)
        self.assertFalse(report.valid)
        paths = {issue.path for issue in report.errors}
        self.assertIn("height_rows[3]", paths)
        self.assertIn("height_rows[4][9]", paths)
        self.assertEqual(candidate, original)

    def test_raised_pin_requires_model_region(self):
        candidate = valid_candidate()
        candidate["region_rows"][10][20] = 0
        report = validate_tactile_candidate(candidate)
        self.assertIn("unowned_raised_pin", {issue.code for issue in report.errors})

    def test_continuous_preview_is_png(self):
        frame = publish_tactile_frame(valid_candidate(), 1)
        png = render_preview_png(frame, scale=2)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_lossless_row_rle_materializes_complete_frame(self):
        candidate = valid_candidate()
        candidate.pop("height_rows")
        candidate.pop("region_rows")
        candidate["height_rows_rle"] = [[[80, 0]] for _ in range(48)]
        candidate["region_rows_rle"] = [[[80, 0]] for _ in range(48)]
        candidate["height_rows_rle"][10] = [[20, 0], [1, 173], [59, 0]]
        candidate["region_rows_rle"][10] = [[20, 0], [1, 1], [59, 0]]
        frame = publish_tactile_frame(candidate, 2)
        self.assertEqual(len(frame.pins), 3840)
        self.assertEqual(frame.height_rows[10][20], 173)

    def test_rle_reports_decoded_row_length(self):
        candidate = valid_candidate()
        candidate.pop("height_rows")
        candidate["height_rows_rle"] = [[[80, 0]] for _ in range(48)]
        candidate["height_rows_rle"][7] = [[79, 0]]
        report = validate_tactile_candidate(candidate)
        self.assertIn("height_rows_rle[7]", {item.path for item in report.errors})


if __name__ == "__main__":
    unittest.main()
