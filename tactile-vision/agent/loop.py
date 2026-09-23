from __future__ import annotations

from typing import Any

from planner.builder import TactileSceneBuilder
from scene.models import SceneUnderstanding, TactilePlan
from tactile.preview import png_to_data_url, render_preview_png

from .critic import VLMCritic


def _review_with_fallback(critic: VLMCritic, **kwargs) -> dict[str, Any]:
    """A network/VLM failure during review must not discard the frames that
    are already live on the surface; keep the round and flag the gap."""
    try:
        return critic.review(**kwargs)
    except Exception as exc:
        return {
            "decision": "review_unavailable",
            "comments": [f"Critic 评审不可用，保留当前轮结果: {exc}"],
        }


def run_compilation_loop(
    provider: Any,
    scene_understanding: SceneUnderstanding,
    plan: TactilePlan,
    profiles: list,
    image_data_url: str,
    image_size: tuple[int, int] | None = None,
    max_revisions: int = 1,
    on_frame=None,
    on_round=None,
) -> dict[str, Any]:
    """Agent closed loop: compile -> review -> revise -> recompile.

    Each round pushes its frame through ``on_frame`` so the pin surface
    refines live (fall, then rise to the new layout).
    """
    if not profiles:
        raise ValueError("at least one device profile is required")
    max_revisions = max(0, min(2, int(max_revisions)))

    builder = TactileSceneBuilder()
    compiler = _LoopCompiler()
    critic = VLMCritic(provider)
    primary_profile = profiles[0]

    iterations: list[dict[str, Any]] = []
    current_plan = plan
    final_scene = None
    final_frames: dict[str, Any] = {}

    for round_index in range(max_revisions + 1):
        scene = builder.build(scene_understanding, current_plan)
        if image_size:
            scene.image_width, scene.image_height = image_size
        frames = {profile.name: compiler.compile(scene, profile) for profile in profiles}
        final_scene = scene
        final_frames = frames

        primary_frame = frames[primary_profile.name]
        frame_summary = compiler.summarize(primary_frame)
        if on_frame is not None:
            on_frame(primary_frame, round_index)

        round_record: dict[str, Any] = {
            "round": round_index,
            "metrics": frame_summary,
            "scene_metrics": scene.metrics,
        }

        if round_index == max_revisions:
            round_record["decision"] = "max_rounds"
            iterations.append(round_record)
            break

        review = _review_with_fallback(
            critic,
            image_data_url=image_data_url,
            preview_data_url=png_to_data_url(render_preview_png(primary_frame)),
            tactile_plan=current_plan.to_dict(),
            metrics=frame_summary,
            device_profile=primary_profile.__dict__,
        )
        decision = str(review.get("decision", "")).strip().casefold()
        round_record["decision"] = decision or "unknown"
        round_record["comments"] = [str(c) for c in review.get("comments", [])][:8]
        iterations.append(round_record)

        if decision != "revise":
            break

        revised = review.get("tactile_plan")
        if not isinstance(revised, dict) or not revised.get("regions"):
            round_record["decision"] = "revise_rejected"
            break
        current_plan = TactilePlan.from_dict(revised)
        if on_round is not None:
            on_round(round_index, current_plan)

    return {
        "tactile_plan": current_plan.to_dict(),
        "tactile_scene": final_scene.to_dict() if final_scene else None,
        "frames": {
            name: compiler.summarize(frame) for name, frame in final_frames.items()
        },
        "region_hit_map": compiler.region_hit_map(),
        "iterations": iterations,
        "primary_device": primary_profile.name,
    }


class _LoopCompiler:
    """Thin wrapper keeping one compiler instance so the region hit map
    always corresponds to the last compiled primary frame."""

    def __init__(self) -> None:
        from compiler.scene_compiler import TactileSceneCompiler

        self._compiler = TactileSceneCompiler()

    def compile(self, scene, profile):
        return self._compiler.compile(scene, profile)

    def summarize(self, frame):
        return self._compiler.summarize(frame)

    def region_hit_map(self):
        return self._compiler.region_hit_map()
