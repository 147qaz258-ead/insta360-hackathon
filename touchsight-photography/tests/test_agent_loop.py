"""端到端集成测试：Mock VLM 脚本化跑通 Agent 全链路并验证存档完整性。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from touchsight.agent.loop import PhotographyAgent
from touchsight.agent.tools import Toolset
from touchsight.panorama.views import PanoramaViewGenerator
from touchsight.storage.runs import RunSession
from touchsight.vlm.providers import ChatResult, MockProvider, ToolCall
from scripts.make_test_pano import generate


def main():
    tmp = Path(tempfile.mkdtemp(prefix="touchsight_test_"))
    pano = generate(tmp / "pano.jpg")

    script = [
        # 1) 先看全局视图
        ChatResult(text="我先观察整个360°场景。", tool_calls=[ToolCall("c1", "get_overview_views", {})]),
        # 2) 朝人物方向取景
        ChatResult(text="view_03 附近有一名蓝色人物，我朝 yaw=120 取景。",
                   tool_calls=[ToolCall("c2", "render_view", {"yaw": 120, "pitch": -5, "fov": 60, "aspect_ratio": "4:3", "reason": "人物特写"})]),
        # 3) 再取一张对比
        ChatResult(text="人物偏暗，我扩大视野带上环境。",
                   tool_calls=[ToolCall("c3", "render_view", {"yaw": 115, "pitch": 0, "fov": 90, "aspect_ratio": "16:9", "reason": "环境人像"})]),
        # 4) 比较两张候选
        ChatResult(text="比较两张候选。",
                   tool_calls=[ToolCall("c4", "inspect_images", {"image_ids": ["candidate_01", "candidate_02"], "question": "哪张人物更突出且背景不杂乱？"})]),
        # 5) 保存最终
        ChatResult(text="candidate_02 人物与环境层次更好，保存。",
                   tool_calls=[ToolCall("c5", "save_image", {"candidate_id": "candidate_02", "reason": "人物突出且保留环境信息"})]),
    ]
    provider = MockProvider(script)
    session = RunSession(tmp / "runs", intent="拍一张人物照片")
    session.save_panorama(pano)
    gen = PanoramaViewGenerator(pano)
    toolset = Toolset(gen, session, ask_user_fn=lambda q: "测试自动回答")
    agent = PhotographyAgent(provider, toolset, session)
    result = agent.run("拍一张人物照片")

    # 验证存档完整性（plan V5 §14）
    run_dir = Path(result["run_dir"])
    checks = {
        "original_panorama": (run_dir / "original_panorama.jpg").exists(),
        "overview_8_views": len(list((run_dir / "overview").glob("view_*.jpg"))) == 8,
        "views_json": (run_dir / "overview" / "views.json").exists(),
        "candidates_2": len(list((run_dir / "candidates").glob("candidate_*.jpg"))) == 2,
        "final": (run_dir / "final.jpg").exists(),
        "trace": (run_dir / "trace.json").exists(),
        "result_final": result["final"] is not None,
    }
    trace = json.loads((run_dir / "trace.json").read_text(encoding="utf-8"))
    event_types = [e["type"] for e in trace]
    checks["trace_has_intent"] = "user_intent" in event_types
    checks["trace_has_tool_calls"] = "tool_call" in event_types
    checks["trace_has_final"] = "final_decision" in event_types
    checks["mock_called_5_times"] = len(provider.calls) == 5

    for k, v in checks.items():
        print(f"  [{'OK ' if v else 'FAIL'}] {k}")
    ok = all(checks.values())
    print(f"\nrun 目录: {run_dir}")
    print("集成测试全部通过" if ok else "集成测试存在失败项！")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
