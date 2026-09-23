from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tactile.preview import png_to_data_url, render_preview_png
from tactile.v4_frame import (
    TactileFrameV2,
    ValidationReport,
    materialize_tactile_candidate,
    publish_tactile_frame,
    validate_tactile_candidate,
)


EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class _PreviewFrame:
    pins: list[int]
    cols: int = 80
    rows: int = 48
    levels: int = 256


def submit_tactile_candidate(candidate: Any) -> ValidationReport:
    """Mechanical tool: report exact contract errors, never repair data."""
    return validate_tactile_candidate(candidate)


def render_tactile_preview(candidate: dict[str, Any]) -> str:
    """Mechanical tool: render precisely the heights supplied by the model."""
    pins = [value for row in candidate["height_rows"] for value in row]
    return png_to_data_url(render_preview_png(_PreviewFrame(pins=pins)))


def _emit(callback: EventCallback | None, event: dict[str, Any]) -> None:
    if callback is not None:
        callback(event)


def _candidate_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
    pins = [value for row in candidate["height_rows"] for value in row]
    active = [value for value in pins if value > 0]
    return {
        "pin_count": len(pins),
        "active_pins": len(active),
        "active_ratio": round(len(active) / len(pins), 4),
        "minimum_height": min(pins) if pins else 0,
        "maximum_height": max(pins) if pins else 0,
        "distinct_heights": len(set(pins)),
        "region_count": len(candidate.get("regions") or []),
    }


def _review_summary(candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_summary": candidate.get("scene_summary"),
        "audio_overview": candidate.get("audio_overview"),
        "regions": candidate.get("regions"),
        "self_review": candidate.get("self_review"),
        "metrics": metrics,
        "hardware_contract": {
            "cols": 80,
            "rows": 48,
            "height_encoding": "uint8",
        },
    }


def run_tactile_agent_loop(
    *,
    provider: Any,
    image_data_url: str,
    frame_id: int,
    user_instruction: str = "",
    max_candidates: int = 3,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """Model-authoritative V4 loop.

    The host validates, renders and publishes. It never rasterizes semantic
    geometry, patches an array or overrides the model's tactile decisions.
    """
    max_candidates = max(1, min(3, int(max_candidates)))
    feedback: list[dict[str, Any]] = []
    previous_candidate: dict[str, Any] | None = None
    previous_preview: str | None = None
    trace: list[dict[str, Any]] = []

    for candidate_number in range(1, max_candidates + 1):
        _emit(on_event, {
            "state": "ANALYZING",
            "candidate": candidate_number,
            "message": f"智能体正在设计第 {candidate_number} 个完整点阵候选",
        })
        candidate = provider.generate_tactile_candidate(
            image_data_url,
            candidate_number=candidate_number,
            user_instruction=user_instruction,
            feedback=feedback,
            previous_candidate=previous_candidate,
            preview_data_url=previous_preview,
        )
        report = submit_tactile_candidate(candidate)
        record: dict[str, Any] = {
            "candidate": candidate_number,
            "validation": report.to_dict(),
        }
        trace.append(record)
        _emit(on_event, {
            "state": "VALIDATING",
            "candidate": candidate_number,
            "message": "正在检查 80×48 硬件协议",
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
            previous_candidate = candidate
            previous_preview = None
            record["decision"] = "protocol_repair"
            continue

        canonical_candidate = materialize_tactile_candidate(candidate)
        preview_data_url = render_tactile_preview(canonical_candidate)
        metrics = _candidate_metrics(canonical_candidate)
        record["metrics"] = metrics
        _emit(on_event, {
            "state": "SELF_REVIEW",
            "candidate": candidate_number,
            "message": "智能体正在对照原图检查实际 80×48 预览",
            "metrics": metrics,
        })
        review = provider.review_tactile_candidate(
            image_data_url,
            preview_data_url,
            candidate_summary=_review_summary(canonical_candidate, metrics),
        )
        decision = str(review.get("decision", "")).strip().casefold()
        record["review"] = review
        record["decision"] = decision or "invalid_review"
        if decision == "accept":
            frame = publish_tactile_frame(candidate, frame_id)
            _emit(on_event, {
                "state": "ACCEPTED",
                "candidate": candidate_number,
                "message": "智能体已接受当前触觉表面",
                "frame_id": frame.frame_id,
                "checksum": frame.checksum,
                "metrics": metrics,
            })
            return {
                "ok": True,
                "frame": frame,
                "candidate": candidate,
                "candidate_count": candidate_number,
                "trace": trace,
                "preview_data_url": preview_data_url,
            }

        if decision == "revise":
            feedback = [{
                "path": "self_review",
                "code": "model_requested_revision",
                "message": str(review.get("revision_guidance") or "；".join(
                    str(item) for item in review.get("comments", [])
                ) or "模型决定重新设计触觉表面"),
            }]
            previous_candidate = candidate
            previous_preview = preview_data_url
            continue

        feedback = [{
            "path": "review.decision",
            "code": "invalid_review_decision",
            "message": "自检必须明确返回 accept 或 revise",
            "actual": review.get("decision"),
        }]
        previous_candidate = candidate
        previous_preview = preview_data_url

    return {
        "ok": False,
        "error": "智能体在三个完整候选内未生成并接受合法触觉帧",
        "candidate_count": max_candidates,
        "trace": trace,
        "last_validation": trace[-1].get("validation") if trace else None,
    }
