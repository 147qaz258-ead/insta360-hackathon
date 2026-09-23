import unittest

from agent.loop import run_compilation_loop
from compiler.profiles import DeviceProfile
from scene.models import SceneUnderstanding, TactilePlan


def make_profile(name="test_hdmi"):
    return DeviceProfile.from_dict({
        "name": name,
        "screen_px_width": 800,
        "screen_px_height": 480,
        "target_pin_count": 3840,
        "height_levels": 4,
    })


def make_plan():
    return TactilePlan.from_dict({
        "version": 1,
        "global_strategy": "test",
        "regions": [{
            "id": "obj",
            "tactile_role": "primary",
            "approx_bbox": [100, 100, 800, 800],
            "boundary_level": 3,
        }],
    })


def make_scene():
    return SceneUnderstanding.from_dict({
        "version": 1,
        "content_type": "photo",
        "scene_summary": "test",
        "regions": [{"id": "obj", "name": "obj"}],
    })


class FakeProvider:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.review_calls = 0

    def critic_review(self, image_data_url, preview_data_url, tactile_plan, metrics, device_profile=None):
        self.review_calls += 1
        if not self.decisions:
            return {"decision": "accept", "comments": []}
        decision = self.decisions.pop(0)
        result = {"decision": decision, "comments": [f"comment-{self.review_calls}"]}
        if decision == "revise":
            result["tactile_plan"] = {
                "version": 1,
                "global_strategy": "revised",
                "regions": [{
                    "id": "obj",
                    "tactile_role": "primary",
                    "approx_bbox": [200, 200, 900, 900],
                    "boundary_level": 3,
                }],
            }
        return result


class CompilationLoopTests(unittest.TestCase):
    def test_accept_on_first_round(self):
        provider = FakeProvider([])
        pushed = []
        result = run_compilation_loop(
            provider=provider,
            scene_understanding=make_scene(),
            plan=make_plan(),
            profiles=[make_profile()],
            image_data_url="data:image/png;base64,xxx",
            max_revisions=2,
            on_frame=lambda frame, round_index: pushed.append((round_index, frame)),
        )
        self.assertEqual(provider.review_calls, 1)
        self.assertEqual(len(pushed), 1)
        self.assertEqual(pushed[0][0], 0)
        self.assertEqual(result["iterations"][0]["decision"], "accept")
        self.assertEqual(result["primary_device"], "test_hdmi")
        self.assertIn("obj", result["region_hit_map"])

    def test_revise_then_accept_compiles_twice(self):
        provider = FakeProvider(["revise", "accept"])
        pushed = []
        result = run_compilation_loop(
            provider=provider,
            scene_understanding=make_scene(),
            plan=make_plan(),
            profiles=[make_profile()],
            image_data_url="data:image/png;base64,xxx",
            max_revisions=2,
            on_frame=lambda frame, round_index: pushed.append(round_index),
        )
        self.assertEqual(provider.review_calls, 2)
        self.assertEqual(pushed, [0, 1])
        self.assertEqual(result["tactile_plan"]["global_strategy"], "revised")
        decisions = [item["decision"] for item in result["iterations"]]
        self.assertEqual(decisions, ["revise", "accept"])

    def test_always_revise_stops_at_max(self):
        provider = FakeProvider(["revise", "revise", "revise"])
        pushed = []
        result = run_compilation_loop(
            provider=provider,
            scene_understanding=make_scene(),
            plan=make_plan(),
            profiles=[make_profile()],
            image_data_url="data:image/png;base64,xxx",
            max_revisions=2,
            on_frame=lambda frame, round_index: pushed.append(round_index),
        )
        self.assertEqual(provider.review_calls, 2)  # no review after final round
        self.assertEqual(pushed, [0, 1, 2])
        self.assertEqual(result["iterations"][-1]["decision"], "max_rounds")

    def test_invalid_revision_keeps_previous_plan(self):
        class BrokenProvider(FakeProvider):
            def critic_review(self, **kwargs):
                self.review_calls += 1
                return {"decision": "revise", "tactile_plan": {"regions": []}}

        provider = BrokenProvider([])
        result = run_compilation_loop(
            provider=provider,
            scene_understanding=make_scene(),
            plan=make_plan(),
            profiles=[make_profile()],
            image_data_url="data:image/png;base64,xxx",
            max_revisions=2,
        )
        self.assertNotEqual(result["tactile_plan"]["global_strategy"], "")
        self.assertEqual(result["iterations"][0]["decision"], "revise_rejected")


if __name__ == "__main__":
    unittest.main()
