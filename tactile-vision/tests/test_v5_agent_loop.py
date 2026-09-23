import copy
import unittest

from agent.v5_loop import evaluate_critic_decision, run_tactile_agent_loop_v5
from tactile.sparse_runs import expand_sparse_candidate
from tactile.v4_frame import publish_tactile_frame


IMAGE_SIZE = (1600, 960)  # content_rect = full 80x48
FAKE_SHOT = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def sparse_candidate(runs, regions=None):
    return {
        "version": "sparse-runs-v1",
        "device_id": "rdk_hdmi",
        "cols": 80,
        "rows": 48,
        "height_encoding": "uint8",
        "content_rect": {"x": 0, "y": 0, "w": 80, "h": 48},
        "regions": regions or [
            {"id": 1, "name": "主体", "description": "主体轮廓", "speech": "这里是主体。"},
        ],
        "raised_runs": runs,
        "scene_summary": "测试场景",
        "audio_overview": "画面中央有一个主体。",
        "design_notes": "用轮廓表达主体，密度低于软目标因内容简单。",
        # 生成者的自评永远不应该影响发布门槛
        "self_review": {"verdict": "perfect", "publishable": True},
    }


def cross_runs(height=200):
    runs = [{"row": 20, "start_col": 30, "end_col": 50, "height": height, "region_id": 1}]
    for row in range(10, 31):
        if row == 20:
            continue
        runs.append({"row": row, "start_col": 40, "end_col": 40, "height": height, "region_id": 1})
    return runs


def solid_block_runs():
    # 人为制造的实心矩形：Critic 必须拒绝这类候选
    return [
        {"row": row, "start_col": 10, "end_col": 70, "height": 255, "region_id": 1}
        for row in range(5, 43)
    ]


def critic_accept(scores=None, density_verdict="密度低于 18% 但内容确实简单，属内容所必需，认可。"):
    base = {
        "composition": 5, "completeness": 5, "spatial": 5, "raised_meaning": 5,
        "separation": 5, "line_quality": 4, "height_layers": 4,
        "complexity_fit": 5, "blind_read_match": 4,
    }
    if scores:
        base.update(scores)
    return {
        "scores": base,
        "critical_defects": [],
        "density_verdict": density_verdict,
        "defects": [],
        "decision": "accept",
        "rationale": "主体清晰可辨。",
    }


def critic_revise(area="主体", location="row 5..42 col 10..70", misread="实心矩形", fix="改为 1~2 点宽轮廓"):
    return {
        "scores": {
            "composition": 2, "completeness": 3, "spatial": 4, "raised_meaning": 1,
            "separation": 1, "line_quality": 2, "height_layers": 2,
            "complexity_fit": 2, "blind_read_match": 1,
        },
        "critical_defects": ["大面积无理由实心块"],
        "density_verdict": "升起率远超 30%，并非内容所必需。",
        "defects": [{"area": area, "location": location, "misread_as": misread, "fix_direction": fix}],
        "decision": "revise",
        "rationale": "实心块无法触摸辨认。",
    }


class FakeV5Provider:
    def __init__(self, candidates, critiques):
        self.candidates = [copy.deepcopy(item) for item in candidates]
        self.critiques = list(critiques)
        self.design_feedback = []
        self.blind_read_inputs = []
        self.critic_inputs = []

    def understand_scene_v5(self, image_data_url):
        return {"scene_summary": "整图理解", "expression_purpose": "认出中央主体"}

    def generate_sparse_candidate(self, image_data_url, **kwargs):
        self.design_feedback.append(kwargs.get("feedback"))
        return self.candidates.pop(0)

    def blind_read_matrix(self, matrix_image_data_url):
        self.blind_read_inputs.append(matrix_image_data_url)
        return {"interpretation": "中央有十字形结构", "overall_readability": 0.8,
                "identified_structures": [], "unreadable_areas": []}

    def critic_review_v5(self, image_data_url, review_page_data_url, **kwargs):
        self.critic_inputs.append({
            "image": image_data_url,
            "review_page": review_page_data_url,
            "kwargs": kwargs,
        })
        return self.critiques.pop(0)


def fake_register(payload):
    return "token-test"


def fake_shot(token, mode):
    assert mode in {"full", "blind"}
    return FAKE_SHOT


def run_loop(provider, frame_id=7):
    return run_tactile_agent_loop_v5(
        provider=provider,
        image_data_url="data:image/png;base64,xxx",
        image_size=IMAGE_SIZE,
        frame_id=frame_id,
        on_event=None,
        register_candidate=fake_register,
        review_shot=fake_shot,
    )


class V5AgentLoopTests(unittest.TestCase):
    def test_accepts_first_valid_candidate_and_publishes(self):
        provider = FakeV5Provider([sparse_candidate(cross_runs())], [critic_accept()])
        result = run_loop(provider, frame_id=7)
        self.assertTrue(result["ok"])
        self.assertEqual(result["candidate_count"], 1)
        frame = result["frame"]
        self.assertEqual(frame.frame_id, 7)
        self.assertEqual(len(frame.pins), 3840)
        self.assertEqual(frame.height_rows[20][40], 200)
        self.assertEqual(frame.region_rows[20][40], 1)
        # checksum 与现有 TactileFrameV2 发布管线完全一致（PC/RDK 同帧）
        expanded = expand_sparse_candidate(sparse_candidate(cross_runs()))
        expected = publish_tactile_frame({
            "version": 2, "device_id": "rdk_hdmi", "cols": 80, "rows": 48,
            "height_encoding": "uint8",
            "height_rows": expanded["height_rows"],
            "region_rows": expanded["region_rows"],
            "regions": [{"id": 1, "name": "主体", "description": "主体轮廓", "speech": "这里是主体。"}],
            "scene_summary": "测试场景",
            "audio_overview": "画面中央有一个主体。",
        }, 7)
        self.assertEqual(frame.checksum, expected.checksum)

    def test_critic_sees_real_review_page_not_preview(self):
        provider = FakeV5Provider([sparse_candidate(cross_runs())], [critic_accept()])
        result = run_loop(provider)
        self.assertTrue(result["ok"])
        self.assertEqual(len(provider.critic_inputs), 1)
        # Critic 收到的是正式渲染器截图（由注入的 review_shot 产生），不是另一套 preview
        self.assertEqual(provider.critic_inputs[0]["review_page"], FAKE_SHOT)
        self.assertEqual(provider.critic_inputs[0]["kwargs"]["metrics"]["pin_count"], 3840)

    def test_blind_read_uses_isolated_matrix_only(self):
        provider = FakeV5Provider([sparse_candidate(cross_runs())], [critic_accept()])
        result = run_loop(provider)
        self.assertTrue(result["ok"])
        self.assertEqual(provider.blind_read_inputs, [FAKE_SHOT])

    def test_protocol_errors_returned_to_next_candidate(self):
        broken = sparse_candidate([
            {"row": 5, "start_col": 10, "end_col": 5, "height": 100, "region_id": 1},
        ])
        provider = FakeV5Provider(
            [broken, sparse_candidate(cross_runs())],
            [critic_accept()],
        )
        result = run_loop(provider)
        self.assertTrue(result["ok"])
        self.assertEqual(result["candidate_count"], 2)
        feedback = provider.design_feedback[1]
        self.assertEqual(feedback[0]["path"], "raised_runs[0].end_col")
        self.assertEqual(feedback[0]["code"], "inverted_range")

    def test_solid_block_rejected_three_times_without_publishing(self):
        provider = FakeV5Provider(
            [sparse_candidate(solid_block_runs()) for _ in range(3)],
            [critic_revise(), critic_revise(), critic_revise()],
        )
        result = run_loop(provider)
        self.assertFalse(result["ok"])
        self.assertNotIn("frame", result)
        self.assertEqual(result["candidate_count"], 3)
        # 每个候选的修订意见必须带 Critic 的定向缺陷
        feedback = provider.design_feedback[1]
        self.assertTrue(any("实心矩形" in item["message"] for item in feedback))

    def test_critic_accept_with_low_scores_blocked_by_gate(self):
        # Critic 自相矛盾（accept 但分数不达标）时，宿主门槛强制 revise
        contradictory = critic_accept(scores={"composition": 2})
        provider = FakeV5Provider(
            [sparse_candidate(cross_runs()), sparse_candidate(cross_runs(height=220))],
            [contradictory, critic_accept()],
        )
        result = run_loop(provider)
        self.assertTrue(result["ok"])
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["frame"].height_rows[20][40], 220)
        feedback = provider.design_feedback[1]
        self.assertTrue(any(item["code"] == "inconsistent_accept" for item in feedback))
        self.assertTrue(any("composition" in item["path"] for item in feedback))

    def test_self_review_cannot_bypass_critic(self):
        # 候选自带“完美自评”，但 Critic 拒绝时必须继续修订
        provider = FakeV5Provider(
            [sparse_candidate(solid_block_runs()), sparse_candidate(cross_runs())],
            [critic_revise(), critic_accept()],
        )
        result = run_loop(provider)
        self.assertTrue(result["ok"])
        self.assertEqual(result["candidate_count"], 2)

    def test_critical_defects_block_accept(self):
        bad = critic_accept()
        bad["critical_defects"] = ["对象全部粘连"]
        accepted, problems = evaluate_critic_decision(bad, active_ratio=0.22)
        self.assertFalse(accepted)
        self.assertTrue(any(p["code"] == "critical_defects_present" for p in problems))

    def test_density_outside_range_requires_explicit_verdict(self):
        silent = critic_accept(density_verdict="")
        accepted, problems = evaluate_critic_decision(silent, active_ratio=0.45)
        self.assertFalse(accepted)
        self.assertTrue(any(p["code"] == "density_unjustified" for p in problems))
        justified = critic_accept(density_verdict="复杂街景确需 45% 密度表达主要结构，认可。")
        accepted, _ = evaluate_critic_decision(justified, active_ratio=0.45)
        self.assertTrue(accepted)

    def test_cancellation_stops_before_next_candidate(self):
        provider = FakeV5Provider(
            [sparse_candidate(solid_block_runs()), sparse_candidate(cross_runs())],
            [critic_revise(), critic_accept()],
        )
        calls = {"count": 0}

        def cancel_after_first():
            calls["count"] += 1
            return calls["count"] > 1

        result = run_tactile_agent_loop_v5(
            provider=provider,
            image_data_url="data:image/png;base64,xxx",
            image_size=IMAGE_SIZE,
            frame_id=1,
            register_candidate=fake_register,
            review_shot=fake_shot,
            is_cancelled=cancel_after_first,
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        # 取消发生在第二候选开始前：只消费了第一个候选
        self.assertEqual(len(provider.candidates), 1)
        self.assertEqual(len(provider.design_feedback), 1)


if __name__ == "__main__":
    unittest.main()
