"""Agent toolset (plan V5 §5): capabilities, not a fixed pipeline."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from touchsight.panorama.views import PanoramaViewGenerator, ViewSpec
from touchsight.storage.runs import RunSession

ASPECTS = {"16:9": (1280, 720), "4:3": (1152, 864), "3:2": (1200, 800), "1:1": (960, 960), "3:4": (864, 1152), "9:16": (720, 1280)}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_panorama",
            "description": "获取当前 X5 拍摄的 360° 全景原图。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_overview_views",
            "description": "获取程序已拆好的全局观察视图（覆盖整个球面，每张带 yaw/pitch/fov 空间元数据）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_view",
            "description": "从 360° 原图按指定取景参数重新渲染一张二维候选照片。每次渲染都会保存为一个候选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "yaw": {"type": "number", "description": "水平朝向，度，0=全景图中心，右转为正，范围[-180,180)"},
                    "pitch": {"type": "number", "description": "俯仰角，度，抬头为正，范围[-90,90]"},
                    "roll": {"type": "number", "description": "绕视轴旋转，度，默认0"},
                    "fov": {"type": "number", "description": "水平视场角，度，越大视野越宽，建议30~120"},
                    "aspect_ratio": {"type": "string", "enum": list(ASPECTS.keys()), "description": "画幅比例"},
                    "reason": {"type": "string", "description": "为什么取这一景（摄影意图）"},
                },
                "required": ["yaw", "pitch", "fov", "aspect_ratio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_images",
            "description": "把一个或多个已有图片（视图或候选）重新调入视野仔细分析比较。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_ids": {"type": "array", "items": {"type": "string"}, "description": "如 [\"candidate_01\",\"view_03\"]"},
                    "question": {"type": "string", "description": "本次重点要看什么"},
                },
                "required": ["image_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_image",
            "description": "从候选中选定最终成片并保存。调用即结束本次拍摄任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string", "description": "如 candidate_01"},
                    "reason": {"type": "string", "description": "为什么它是最终照片（意图匹配、构图、光线、技术质量）"},
                },
                "required": ["candidate_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "拍摄意图不明确时追问用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用中文向用户提出的问题"},
                },
                "required": ["question"],
            },
        },
    },
]


class Toolset:
    def __init__(
        self,
        generator: PanoramaViewGenerator,
        session: RunSession,
        ask_user_fn: Callable[[str], str] | None = None,
    ):
        self.generator = generator
        self.session = session
        self.ask_user_fn = ask_user_fn or (lambda q: input(f"\n[Agent 提问] {q}\n> "))
        self.overview_entries: list[dict] = []
        self.candidates: dict[str, Path] = {}
        self.candidate_meta: dict[str, dict] = {}
        self.final_candidate: Path | None = None
        self.final_reason: str = ""
        self._candidate_n = 0

    def _overview(self) -> tuple[str, list[Path]]:
        if not self.overview_entries:
            self.overview_entries = self.generator.generate_overview(self.session.run_dir / "overview")
            self.session.log("overview_generated", self.overview_entries)
        meta = json.dumps(
            [{k: e[k] for k in ("view_id", "yaw", "pitch", "roll", "fov")} for e in self.overview_entries],
            ensure_ascii=False,
        )
        return f"全局观察视图（{len(self.overview_entries)}张，覆盖整个球面），空间元数据：{meta}", [
            Path(e["path"]) for e in self.overview_entries
        ]

    def _render(self, args: dict) -> tuple[str, list[Path]]:
        aspect = args.get("aspect_ratio", "4:3")
        width, height = ASPECTS.get(aspect, ASPECTS["4:3"])
        self._candidate_n += 1
        cid = f"candidate_{self._candidate_n:02d}"
        spec = ViewSpec(
            view_id=cid,
            yaw=float(args.get("yaw", 0)),
            pitch=float(args.get("pitch", 0)),
            roll=float(args.get("roll", 0)),
            fov=float(args.get("fov", 60)),
            width=width,
            height=height,
        )
        path = self.generator.render_to_file(spec, self.session.run_dir / "candidates")
        self.candidates[cid] = path
        self.candidate_meta[cid] = {**asdict(spec), "aspect_ratio": aspect, "reason": args.get("reason", "")}
        self.session.log("candidate_rendered", self.candidate_meta[cid])
        return (
            f"已渲染 {cid}：yaw={spec.yaw}°, pitch={spec.pitch}°, roll={spec.roll}°, "
            f"fov={spec.fov}°, 画幅={aspect}。请仔细观察这张照片并依据摄影评审标准评估它。"
        ), [path]

    def _inspect(self, args: dict) -> tuple[str, list[Path]]:
        paths: list[Path] = []
        for iid in args.get("image_ids", []):
            if iid in self.candidates:
                paths.append(self.candidates[iid])
            else:
                for e in self.overview_entries:
                    if e["view_id"] == iid:
                        paths.append(Path(e["path"]))
        if not paths:
            return "未找到指定图片 ID。", []
        question = args.get("question", "")
        return f"重新调入 {len(paths)} 张图片供比较分析。{('重点：' + question) if question else ''}", paths

    def _save(self, args: dict) -> tuple[str, list[Path]]:
        cid = args.get("candidate_id", "")
        if cid not in self.candidates:
            return f"候选 {cid} 不存在，无法保存。现有候选：{list(self.candidates)}", []
        self.final_candidate = self.candidates[cid]
        self.final_reason = args.get("reason", "")
        return f"已选定 {cid} 为最终成片。", []

    def _ask_user(self, args: dict) -> tuple[str, list[Path]]:
        answer = self.ask_user_fn(args.get("question", ""))
        self.session.log("ask_user", {"question": args.get("question"), "answer": answer})
        return f"用户回答：{answer}", []

    def execute(self, name: str, args: dict) -> tuple[str, list[Path]]:
        if name == "get_panorama":
            self.session.log("tool_call", {"name": name})
            return "当前 360° 全景原图：", [self.generator.panorama_path]
        if name == "get_overview_views":
            self.session.log("tool_call", {"name": name})
            return self._overview()
        self.session.log("tool_call", {"name": name, "args": args})
        if name == "render_view":
            return self._render(args)
        if name == "inspect_images":
            return self._inspect(args)
        if name == "save_image":
            return self._save(args)
        if name == "ask_user":
            return self._ask_user(args)
        return f"未知工具：{name}", []
