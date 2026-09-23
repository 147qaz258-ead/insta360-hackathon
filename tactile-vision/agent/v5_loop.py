from __future__ import annotations

import time
from typing import Any, Callable

from tactile.sparse_runs import (
    compute_content_rect,
    compute_sparse_metrics,
    expand_sparse_candidate,
    validate_sparse_candidate,
)
from tactile.v4_frame import TactileFrameV2, publish_tactile_frame


EventCallback = Callable[[dict[str, Any]], None]
ReviewShot = Callable[[str, str], str]
CandidateRegistry = Callable[[dict[str, Any]], str]
CancelCheck = Callable[[], bool]

CORE_SCORE_KEYS = ("composition", "completeness", "raised_meaning", "separation", "line_quality")
SECONDARY_SCORE_KEYS = ("height_layers", "blind_read_match")
ALL_SCORE_KEYS = CORE_SCORE_KEYS + SECONDARY_SCORE_KEYS + ("spatial", "complexity_fit")
DENSITY_RANGE = (0.18, 0.30)


def _emit(callback: EventCallback | None, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)


def evaluate_critic_decision(
    critique: dict[str, Any],
    *,
    active_ratio: float,
) -> tuple[bool, list[dict[str, Any]]]:
    """Mechanically enforce the acceptance gate on the Critic's JSON.

    The Critic decides; the host only checks that the decision is consistent
    with the published acceptance rules. The generator's own self-review is
    never part of this gate.
    """
    problems: list[dict[str, Any]] = []
    scores = critique.get("scores")
    if not isinstance(scores, dict):
        problems.append({
            "path": "critic.scores",
            "code": "missing_scores",
            "message": "评审缺少逐项评分，无法验证接受门槛",
        })
        scores = {}
    for key in CORE_SCORE_KEYS:
        value = scores.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 4:
            problems.append({
                "path": f"critic.scores.{key}",
                "code": "below_threshold",
                "message": f"{key} 必须 ≥4 才能发布",
                "actual": value,
            })
    for key in SECONDARY_SCORE_KEYS:
        value = scores.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 3:
            problems.append({
                "path": f"critic.scores.{key}",
                "code": "below_threshold",
                "message": f"{key} 必须 ≥3 才能发布",
                "actual": value,
            })
    critical = critique.get("critical_defects")
    if isinstance(critical, list) and any(str(item).strip() for item in critical):
        problems.append({
            "path": "critic.critical_defects",
            "code": "critical_defects_present",
            "message": "；".join(str(item) for item in critical if str(item).strip()),
        })
    if not (DENSITY_RANGE[0] <= active_ratio <= DENSITY_RANGE[1]):
        verdict = critique.get("density_verdict")
        if not isinstance(verdict, str) or len(verdict.strip()) < 8:
            problems.append({
                "path": "critic.density_verdict",
                "code": "density_unjustified",
                "message": (
                    f"升起率 {active_ratio:.1%} 超出 18%~30% 软目标，"
                    "Critic 必须明确说明该密度为内容所必需，否则不得 accept"
                ),
            })

    decision = str(critique.get("decision", "")).strip().casefold()
    if decision == "accept" and not problems:
        return True, []
    if decision == "accept" and problems:
        problems.insert(0, {
            "path": "critic.decision",
            "code": "inconsistent_accept",
            "message": "评审决定 accept，但以下接受门槛未满足；请整体修订后重交",
        })
    elif decision != "revise":
        problems.insert(0, {
            "path": "critic.decision",
            "code": "invalid_decision",
            "message": f"评审决定必须是 accept 或 revise，实际为 {critique.get('decision')!r}",
        })
    return False, problems


def _revision_feedback(critique: dict[str, Any], gate_problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    feedback: list[dict[str, Any]] = []
    defects = critique.get("defects")
    if isinstance(defects, list):
        for index, defect in enumerate(defects[:12]):
            if isinstance(defect, dict):
                feedback.append({
                    "path": f"critic.defects[{index}]",
                    "code": "targeted_defect",
                    "message": (
                        f"区域 {defect.get('area', '?')}（{defect.get('location', '?')}）"
                        f"当前被误读为：{defect.get('misread_as', '?')}；"
                        f"修订方向：{defect.get('fix_direction', '?')}"
                    ),
                })
    for problem in gate_problems[:10]:
        feedback.append(problem)
    scores = critique.get("scores")
    if isinstance(scores, dict):
        feedback.append({
            "path": "critic.scores",
            "code": "score_summary",
            "message": "；".join(f"{key}={scores.get(key, '?')}" for key in ALL_SCORE_KEYS),
        })
    rationale = str(critique.get("rationale") or "").strip()
    if rationale:
        feedback.append({"path": "critic.rationale", "code": "rationale", "message": rationale[:400]})
    verdict = str(critique.get("density_verdict") or "").strip()
    if verdict:
        feedback.append({"path": "critic.density_verdict", "code": "density", "message": verdict[:300]})
    return feedback or [{
        "path": "critic",
        "code": "unstructured_revision",
        "message": "评审要求重新设计，但未给出结构化缺陷；请整体重做并优先保证主体轮廓可辨、结构分离。",
    }]


def run_tactile_agent_loop_v5(
    *,
    provider: Any,
    image_data_url: str,
    image_size: tuple[int, int],
    frame_id: int,
    user_instruction: str = "",
    max_candidates: int = 3,
    on_event: EventCallback | None = None,
    register_candidate: CandidateRegistry | None = None,
    review_shot: ReviewShot | None = None,
    is_cancelled: CancelCheck | None = None,
) -> dict[str, Any]:
    """V5 generate → review → critique closed loop.

    The model authors every raised pin in one global sparse-runs submission;
    the host only expands and validates mechanically, renders the candidate on
    the real product page, runs an isolated blind read and an isolated Critic,
    and publishes only Critic-approved frames.
    """
    max_candidates = max(1, min(3, int(max_candidates)))
    content_rect = compute_content_rect(int(image_size[0]), int(image_size[1]))
    feedback: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    started_at = time.monotonic()

    def cancelled() -> bool:
        return bool(is_cancelled and is_cancelled())

    _emit(on_event, {
        "state": "UNDERSTANDING",
        "candidate": 0,
        "message": "智能体正在整图理解并决定表达目的",
    })
    understanding = provider.understand_scene_v5(image_data_url)
    understanding_notes = {
        "scene_summary": understanding.get("scene_summary"),
        "expression_purpose": understanding.get("expression_purpose"),
    }

    for candidate_number in range(1, max_candidates + 1):
        if cancelled():
            return {"ok": False, "cancelled": True, "error": "运行已被新请求取消", "trace": trace,
                    "candidate_count": candidate_number - 1}
        record: dict[str, Any] = {"candidate": candidate_number, "stages": {}}
        trace.append(record)

        stage_start = time.monotonic()
        _emit(on_event, {
            "state": "DESIGNING",
            "candidate": candidate_number,
            "message": f"智能体正在一次性设计第 {candidate_number} 个全局点段候选",
        })
        candidate = provider.generate_sparse_candidate(
            image_data_url,
            understanding=understanding,
            content_rect=content_rect,
            candidate_number=candidate_number,
            user_instruction=user_instruction,
            feedback=feedback,
        )
        record["stages"]["design_seconds"] = round(time.monotonic() - stage_start, 2)
        record["understanding"] = understanding_notes
        record["design_notes"] = candidate.get("design_notes") if isinstance(candidate, dict) else None
        record["sparse_candidate"] = candidate

        stage_start = time.monotonic()
        report = validate_sparse_candidate(candidate, content_rect)
        record["validation"] = report.to_dict()
        record["stages"]["validate_seconds"] = round(time.monotonic() - stage_start, 2)
        _emit(on_event, {
            "state": "VALIDATING",
            "candidate": candidate_number,
            "message": "正在机械校验 sparse-runs-v1 协议",
            "validation": report.to_dict(),
        })
        if not report.valid:
            feedback = [issue.to_dict() for issue in report.errors[:80]]
            if len(report.errors) > 80:
                feedback.append({
                    "path": "$",
                    "code": "additional_errors",
                    "message": f"另有 {len(report.errors) - 80} 个同类机械错误；先修复以上结构问题后重新完整校验",
                })
            record["decision"] = "protocol_repair"
            continue

        expanded = expand_sparse_candidate(candidate)
        height_rows = expanded["height_rows"]
        region_rows = expanded["region_rows"]
        regions = candidate.get("regions") or []
        metrics = compute_sparse_metrics(height_rows, region_rows, regions, content_rect)
        record["metrics"] = metrics

        stage_start = time.monotonic()
        _emit(on_event, {
            "state": "RENDERING",
            "candidate": candidate_number,
            "message": "正式渲染器正在生成候选审查页（原图 + 主点阵 + 2.5D 辅图 + 指标）",
            "metrics": metrics,
        })
        if register_candidate is None or review_shot is None:
            raise RuntimeError("V5 闭环需要 register_candidate 与 review_shot 才能生成真实审查页")
        review_token = register_candidate({
            "image_data_url": image_data_url,
            "candidate_number": candidate_number,
            "frame": {
                "cols": 80,
                "rows": 48,
                "pins": [value for row in height_rows for value in row],
                "height_rows": height_rows,
                "region_rows": region_rows,
                "regions": regions,
            },
            "metrics": metrics,
        })
        record["review_token"] = review_token
        review_page_data_url = review_shot(review_token, "full")
        blind_matrix_data_url = review_shot(review_token, "blind")
        record["stages"]["render_seconds"] = round(time.monotonic() - stage_start, 2)

        stage_start = time.monotonic()
        _emit(on_event, {
            "state": "BLIND_READING",
            "candidate": candidate_number,
            "message": "独立读者正在无原图盲读黑白点阵",
        })
        blind_read = provider.blind_read_matrix(blind_matrix_data_url)
        record["blind_read"] = blind_read
        record["stages"]["blind_read_seconds"] = round(time.monotonic() - stage_start, 2)

        stage_start = time.monotonic()
        _emit(on_event, {
            "state": "CRITIQUING",
            "candidate": candidate_number,
            "message": "独立 Critic 正在对照原图、真实审查页与机械指标逐项评分",
        })
        critique = provider.critic_review_v5(
            image_data_url,
            review_page_data_url,
            blind_read=blind_read,
            metrics=metrics,
            design_notes=str(candidate.get("design_notes") or ""),
            user_instruction=user_instruction,
        )
        record["critique"] = critique
        record["stages"]["critic_seconds"] = round(time.monotonic() - stage_start, 2)

        accepted, gate_problems = evaluate_critic_decision(
            critique, active_ratio=float(metrics.get("active_ratio", 0.0))
        )
        record["gate_problems"] = gate_problems
        record["decision"] = "accept" if accepted else "revise"

        if accepted:
            frame_candidate = {
                "version": 2,
                "device_id": "rdk_hdmi",
                "cols": 80,
                "rows": 48,
                "height_encoding": "uint8",
                "height_rows": height_rows,
                "region_rows": region_rows,
                "regions": regions,
                "scene_summary": str(candidate.get("scene_summary") or understanding.get("scene_summary") or ""),
                "audio_overview": str(candidate.get("audio_overview") or ""),
                "self_review": {
                    "protocol": "sparse-runs-v1",
                    "design_notes": candidate.get("design_notes"),
                    "expression_purpose": understanding.get("expression_purpose"),
                    "blind_read": blind_read,
                    "critic_scores": critique.get("scores"),
                    "critic_rationale": critique.get("rationale"),
                    "density_verdict": critique.get("density_verdict"),
                },
            }
            frame = publish_tactile_frame(frame_candidate, frame_id)
            record["frame_checksum"] = frame.checksum
            record["total_seconds"] = round(time.monotonic() - started_at, 2)
            _emit(on_event, {
                "state": "ACCEPTED",
                "candidate": candidate_number,
                "message": "Critic 已通过当前触觉表面，正在发布权威帧",
                "frame_id": frame.frame_id,
                "checksum": frame.checksum,
                "metrics": metrics,
                "critique": critique,
            })
            return {
                "ok": True,
                "frame": frame,
                "candidate": candidate,
                "candidate_count": candidate_number,
                "trace": trace,
                "understanding": understanding,
                "metrics": metrics,
                "blind_read": blind_read,
                "critique": critique,
                "review_token": review_token,
            }

        feedback = _revision_feedback(critique, gate_problems)
        _emit(on_event, {
            "state": "REVISING",
            "candidate": candidate_number,
            "message": f"Critic 未通过第 {candidate_number} 候选，已给出定向修订意见",
            "critique": critique,
        })

    return {
        "ok": False,
        "error": "智能体在三个完整候选内未通过 Critic 评审；已保留上一合法帧",
        "candidate_count": max_candidates,
        "trace": trace,
        "understanding": understanding,
        "last_validation": trace[-1].get("validation") if trace else None,
    }
