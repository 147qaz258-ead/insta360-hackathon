from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_local_environment


load_local_environment()


from app.server import run_server
from compiler.baseline import BaselineConfig, extract_structural_map, load_grayscale, save_binary_map
from compiler.profiles import DeviceProfile
from tactile.protocol import encode_frame
from tactile.rasterizer import AdaptiveRasterizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tactile Compiler multimodal product runtime")
    parser.add_argument(
        "--input",
        type=Path,
        help="Legacy structural baseline diagnostic only; normal product input comes from the web/API.",
    )
    parser.add_argument(
        "--device",
        action="append",
        dest="devices",
        help="Device profile name; repeat to compile multiple outputs",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address. 0.0.0.0 allows other devices on the LAN to open the page.",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--frame-id", type=int, default=0)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compile, print summaries, then exit without the web server.",
    )
    return parser.parse_args()


def load_profile(name: str) -> DeviceProfile:
    path = ROOT / "devices" / "profiles" / f"{name}.json"
    if not path.is_file():
        raise SystemExit(f"Device profile not found: {path}")
    return DeviceProfile.load(path)


def frame_summary(frame) -> dict:
    active = sum(1 for pin in frame.pins if pin)
    return {
        "grid": f"{frame.cols}x{frame.rows}",
        "pins": len(frame.pins),
        "active": active,
        "active_pin_ratio": round(active / len(frame.pins), 4),
        "content_box": frame.content_box,
        "source_size": frame.source_size,
        "frame_id": frame.frame_id,
    }


def main() -> int:
    args = parse_args()
    device_names = args.devices or ["rdk_hdmi"]
    profiles = [load_profile(name) for name in device_names]
    rasterizer = AdaptiveRasterizer()

    if args.input:
        baseline_config = BaselineConfig()
        grayscale = load_grayscale(args.input, baseline_config.max_input_dimension)
        structural_map = extract_structural_map(grayscale, baseline_config)
        frames = {}
        for current_profile in profiles:
            frame = rasterizer.rasterize(structural_map, current_profile)
            frame.frame_id = args.frame_id
            frames[current_profile.name] = frame

        args.output_dir.mkdir(parents=True, exist_ok=True)
        save_binary_map(structural_map, args.output_dir / "structural_map.png")
        summaries = {}
        for current_profile in profiles:
            frame = frames[current_profile.name]
            stem = f"{args.input.stem}.{current_profile.name}"
            (args.output_dir / f"{stem}.frame.json").write_text(
                json.dumps(frame.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (args.output_dir / f"{stem}.tcf").write_bytes(encode_frame(frame))
            summaries[current_profile.name] = frame_summary(frame)

        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        if args.check:
            return 0

        profile = profiles[0]
        primary_frame = frames[profile.name]

        def frame_factory(_pattern: str):
            return primary_frame

        mode = f"baseline image: {args.input.name}"
        available_patterns: list[str] = []
    else:
        profile = profiles[0]
        cols, rows = profile.compute_grid()

        def frame_factory(_pattern: str):
            from tactile.frame import TactileFrame

            return TactileFrame(
                version=1,
                cols=cols,
                rows=rows,
                levels=max(2, profile.height_levels),
                aspect_ratio=cols / rows,
                pins=[0] * (cols * rows),
                frame_id=args.frame_id,
            )

        if args.check:
            print(json.dumps({
                "mode": "multimodal_product_runtime",
                "device": profile.name,
                "grid": f"{cols}x{rows}",
            }, ensure_ascii=False, indent=2))
            return 0

        mode = "multimodal product pipeline"
        available_patterns: list[str] = []

    cols, rows = profile.compute_grid()
    info = {
        "mode": mode,
        "device": profile.name,
        "screen": {
            "width": profile.screen_px_width,
            "height": profile.screen_px_height,
        },
        "grid": {"cols": cols, "rows": rows},
        "target_pin_count": profile.target_pin_count,
        "height_levels": profile.height_levels,
        "available_patterns": available_patterns,
        "capabilities": [
            "qwen3.8_max_global_sparse_runs_authoring",
            "model_authoritative_80x48_frame",
            "sparse_runs_v1_one_shot_protocol",
            "three_candidate_agent_tool_loop",
            "real_review_page_critic_loop",
            "isolated_blind_read_check",
            "mechanical_validation_without_semantic_recompile",
            "touch_voice_explanation",
            "tactile_frame_v2_uint8",
            "crc32_uint8_wire_protocol",
            "continuous_height_3d_pin_renderer",
            "pc_rdk_same_frame_runtime",
        ],
    }

    run_server(
        host=args.host,
        port=args.port,
        web_root=ROOT / "renderer" / "web",
        frame_factory=frame_factory,
        profile_info=info,
        profiles=profiles,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
