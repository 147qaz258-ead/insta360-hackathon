import copy
import unittest

from agent.v4_loop import run_tactile_agent_loop


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


class FakeV4Provider:
    def __init__(self, candidates, decisions):
        self.candidates = [copy.deepcopy(item) for item in candidates]
        self.decisions = list(decisions)
        self.feedback = []

    def generate_tactile_candidate(self, image_data_url, **kwargs):
        self.feedback.append(kwargs.get("feedback"))
        return self.candidates.pop(0)

    def review_tactile_candidate(self, image_data_url, preview_data_url, **kwargs):
        decision = self.decisions.pop(0)
        return {"decision": decision, "comments": [decision], "revision_guidance": "整体重做"}


class V4AgentLoopTests(unittest.TestCase):
    def test_accepts_first_valid_candidate(self):
        provider = FakeV4Provider([valid_candidate()], ["accept"])
        events = []
        result = run_tactile_agent_loop(
            provider=provider,
            image_data_url="data:image/png;base64,xxx",
            frame_id=8,
            on_event=events.append,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["frame"].frame_id, 8)
        self.assertIn("SELF_REVIEW", [event["state"] for event in events])

    def test_returns_exact_validation_errors_to_next_candidate(self):
        broken = valid_candidate()
        broken["height_rows"][2] = broken["height_rows"][2][:-2]
        provider = FakeV4Provider([broken, valid_candidate()], ["accept"])
        result = run_tactile_agent_loop(
            provider=provider,
            image_data_url="data:image/png;base64,xxx",
            frame_id=9,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(provider.feedback[1][0]["path"], "height_rows[2]")

    def test_three_revisions_fail_without_publishing(self):
        provider = FakeV4Provider(
            [valid_candidate(), valid_candidate(), valid_candidate()],
            ["revise", "revise", "revise"],
        )
        result = run_tactile_agent_loop(
            provider=provider,
            image_data_url="data:image/png;base64,xxx",
            frame_id=10,
        )
        self.assertFalse(result["ok"])
        self.assertNotIn("frame", result)
        self.assertEqual(result["candidate_count"], 3)


if __name__ == "__main__":
    unittest.main()
