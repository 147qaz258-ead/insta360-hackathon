from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


@dataclass
class SceneRegion:
    id: str
    name: str
    kind: str = "other"
    approx_location: str = ""
    importance: float = 0.0
    structural_importance: float = 0.0
    needs_precise_grounding: bool = True
    grounding_query: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneRegion":
        return cls(
            id=str(data.get("id", "")).strip(),
            name=str(data.get("name", "")).strip(),
            kind=str(data.get("kind", "other")).strip(),
            approx_location=str(data.get("approx_location", "")).strip(),
            importance=_clamp01(data.get("importance")),
            structural_importance=_clamp01(data.get("structural_importance")),
            needs_precise_grounding=bool(data.get("needs_precise_grounding", True)),
            grounding_query=str(data.get("grounding_query", "")).strip(),
            notes=str(data.get("notes", "")).strip(),
        )


@dataclass
class SceneRelation:
    subject: str
    relation: str
    object: str
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneRelation":
        return cls(
            subject=str(data.get("subject", "")).strip(),
            relation=str(data.get("relation", "other")).strip(),
            object=str(data.get("object", "")).strip(),
            confidence=_clamp01(data.get("confidence")),
        )


@dataclass
class SceneUnderstanding:
    version: int
    content_type: str
    scene_summary: str
    global_layout: dict[str, Any]
    regions: list[SceneRegion] = field(default_factory=list)
    relations: list[SceneRelation] = field(default_factory=list)
    text_content: list[dict[str, Any]] = field(default_factory=list)
    grounding_prompts: list[str] = field(default_factory=list)
    tactile_considerations: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SceneUnderstanding":
        regions = [SceneRegion.from_dict(item) for item in data.get("regions", []) if isinstance(item, dict)]
        relations = [SceneRelation.from_dict(item) for item in data.get("relations", []) if isinstance(item, dict)]

        seen: set[str] = set()
        prompts: list[str] = []
        for prompt in data.get("grounding_prompts", []):
            value = str(prompt).strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                prompts.append(value)

        return cls(
            version=int(data.get("version", 1)),
            content_type=str(data.get("content_type", "other")),
            scene_summary=str(data.get("scene_summary", "")).strip(),
            global_layout=data.get("global_layout", {}) if isinstance(data.get("global_layout", {}), dict) else {},
            regions=regions,
            relations=relations,
            text_content=[item for item in data.get("text_content", []) if isinstance(item, dict)],
            grounding_prompts=prompts,
            tactile_considerations=[str(item) for item in data.get("tactile_considerations", [])],
            uncertainties=[str(item) for item in data.get("uncertainties", [])],
            meta=data.get("_meta", {}) if isinstance(data.get("_meta", {}), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "content_type": self.content_type,
            "scene_summary": self.scene_summary,
            "global_layout": self.global_layout,
            "regions": [vars(region) for region in self.regions],
            "relations": [vars(relation) for relation in self.relations],
            "text_content": self.text_content,
            "grounding_prompts": self.grounding_prompts,
            "tactile_considerations": self.tactile_considerations,
            "uncertainties": self.uncertainties,
            "_meta": self.meta,
        }


@dataclass
class GroundedRegion:
    id: str
    bbox_xyxy: list[float] | None = None
    mask_ref: str | None = None
    contour: list[list[float]] = field(default_factory=list)
    confidence: float = 0.0
    source: str = ""
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroundedScene:
    version: int = 1
    image_width: int = 0
    image_height: int = 0
    regions: list[GroundedRegion] = field(default_factory=list)
    lines: list[dict[str, Any]] = field(default_factory=list)
    text_regions: list[dict[str, Any]] = field(default_factory=list)
    depth_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class TactilePlan:
    version: int
    global_strategy: str
    regions: list[dict[str, Any]]
    preserve_relations: list[str]
    global_landmarks: list[str]
    compression_rules: list[str]
    warnings: list[str]
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TactilePlan":
        return cls(
            version=int(data.get("version", 1)),
            global_strategy=str(data.get("global_strategy", "")).strip(),
            regions=[item for item in data.get("regions", []) if isinstance(item, dict)],
            preserve_relations=[str(item) for item in data.get("preserve_relations", [])],
            global_landmarks=[str(item) for item in data.get("global_landmarks", [])],
            compression_rules=[str(item) for item in data.get("compression_rules", [])],
            warnings=[str(item) for item in data.get("warnings", [])],
            meta=data.get("_meta", {}) if isinstance(data.get("_meta", {}), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "global_strategy": self.global_strategy,
            "regions": self.regions,
            "preserve_relations": self.preserve_relations,
            "global_landmarks": self.global_landmarks,
            "compression_rules": self.compression_rules,
            "warnings": self.warnings,
            "_meta": self.meta,
        }


VALID_GEOMETRY_TYPES = {"polygon", "polyline", "ellipse", "rect", "point"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_program_geometry(data: Any) -> dict[str, Any]:
    """Keep model-authored geometry, but make its device-independent contract safe.

    Coordinates remain in the program's 0..coordinate_space canvas. Conversion
    to concrete pins belongs to the deterministic DeviceCompiler.
    """
    raw = data if isinstance(data, dict) else {}
    geometry_type = str(raw.get("type", "rect")).strip().casefold()
    if geometry_type not in VALID_GEOMETRY_TYPES:
        geometry_type = "rect"

    points: list[list[float]] = []
    for point in raw.get("points", []):
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            points.append([_number(point[0]), _number(point[1])])

    bbox: list[float] = []
    candidate_bbox = raw.get("bbox", [])
    if isinstance(candidate_bbox, (list, tuple)) and len(candidate_bbox) == 4:
        bbox = [_number(value) for value in candidate_bbox]

    fill = str(raw.get("fill", "none")).strip().casefold()
    if fill not in {"none", "sparse", "solid"}:
        fill = "none"
    thickness = max(1, min(6, int(round(_number(raw.get("thickness"), 1)))))
    return {
        "type": geometry_type,
        "points": points,
        "bbox": bbox,
        "thickness": thickness,
        "fill": fill,
    }


@dataclass
class TactileProgramRegion:
    id: str
    name: str
    description: str
    speech: str
    importance: float
    height: int
    tactile_role: str
    geometry: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TactileProgramRegion":
        region_id = str(data.get("id", "")).strip()
        name = str(data.get("name", "")).strip() or region_id
        try:
            height = int(round(float(data.get("height", 2))))
        except (TypeError, ValueError):
            height = 2
        return cls(
            id=region_id,
            name=name,
            description=str(data.get("description", "")).strip(),
            speech=str(data.get("speech", "")).strip(),
            importance=_clamp01(data.get("importance"), 0.5),
            height=max(1, min(3, height)),
            tactile_role=str(data.get("tactile_role", "secondary")).strip() or "secondary",
            geometry=_normalize_program_geometry(data.get("geometry")),
        )

    def to_dict(self) -> dict[str, Any]:
        return vars(self).copy()


@dataclass
class TactileProgram:
    """Single multimodal-agent output consumed by the DeviceCompiler.

    It deliberately combines whole-image understanding, tactile geometry and
    interaction speech. A successful generation therefore cannot stop at a
    prose-only scene report.
    """

    version: int
    content_type: str
    scene_summary: str
    audio_overview: str
    canvas: dict[str, Any]
    regions: list[TactileProgramRegion]
    relations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TactileProgram":
        canvas = data.get("canvas", {}) if isinstance(data.get("canvas"), dict) else {}
        try:
            coordinate_space = int(canvas.get("coordinate_space", 1000))
        except (TypeError, ValueError):
            coordinate_space = 1000
        coordinate_space = max(100, min(10000, coordinate_space))
        orientation = str(canvas.get("orientation", "landscape")).strip().casefold()
        if orientation not in {"landscape", "portrait", "square"}:
            orientation = "landscape"

        regions: list[TactileProgramRegion] = []
        seen: set[str] = set()
        for item in data.get("regions", []):
            if not isinstance(item, dict):
                continue
            region = TactileProgramRegion.from_dict(item)
            if not region.id or region.id in seen:
                continue
            seen.add(region.id)
            regions.append(region)
        if not regions:
            raise ValueError("TactileProgram must contain at least one region")

        scene_summary = str(data.get("scene_summary", "")).strip()
        audio_overview = str(data.get("audio_overview", "")).strip() or scene_summary
        return cls(
            version=int(data.get("version", 1)),
            content_type=str(data.get("content_type", "other")).strip() or "other",
            scene_summary=scene_summary,
            audio_overview=audio_overview,
            canvas={"coordinate_space": coordinate_space, "orientation": orientation},
            regions=regions,
            relations=[str(item).strip() for item in data.get("relations", []) if str(item).strip()],
            warnings=[str(item).strip() for item in data.get("warnings", []) if str(item).strip()],
            meta=data.get("_meta", {}) if isinstance(data.get("_meta"), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "content_type": self.content_type,
            "scene_summary": self.scene_summary,
            "audio_overview": self.audio_overview,
            "canvas": self.canvas,
            "regions": [region.to_dict() for region in self.regions],
            "relations": self.relations,
            "warnings": self.warnings,
            "_meta": self.meta,
        }
