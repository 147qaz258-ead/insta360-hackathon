from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MAX_SEMANTIC_LEVEL = 3

VALID_FILL_MODES = {"none", "sparse", "solid"}
VALID_SOURCES = {"approximate", "grounded", "unplaced"}


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_level(value: Any, default: int = 0) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(0, min(MAX_SEMANTIC_LEVEL, number))


def _normalize_contour(raw: Any) -> list[list[float]]:
    points: list[list[float]] = []
    if not isinstance(raw, list):
        return points
    for point in raw:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x = _clamp(point[0], 0.0, 1.0, 0.0)
            y = _clamp(point[1], 0.0, 1.0, 0.0)
            points.append([x, y])
    return points


@dataclass
class TactileRegion:
    id: str
    name: str
    tactile_role: str = "secondary"
    boundary_level: int = 2
    internal_level: int = 0
    fill_mode: str = "none"
    contours: list[list[list[float]]] = field(default_factory=list)
    landmarks: list[dict[str, Any]] = field(default_factory=list)
    source: str = "approximate"
    notes: str = ""
    description: str = ""
    speech: str = ""
    importance: float = 0.0
    closed: bool = True
    thickness: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TactileRegion":
        fill_mode = str(data.get("fill_mode", "none")).strip().casefold()
        if fill_mode not in VALID_FILL_MODES:
            fill_mode = "none"
        source = str(data.get("source", "approximate")).strip().casefold()
        if source not in VALID_SOURCES:
            source = "approximate"
        contours = [
            _normalize_contour(contour)
            for contour in data.get("contours", [])
            if isinstance(contour, list)
        ]
        landmarks = [item for item in data.get("landmarks", []) if isinstance(item, dict)]
        return cls(
            id=str(data.get("id", "")).strip(),
            name=str(data.get("name", "")).strip(),
            tactile_role=str(data.get("tactile_role", "secondary")).strip(),
            boundary_level=_clamp_level(data.get("boundary_level")),
            internal_level=_clamp_level(data.get("internal_level")),
            fill_mode=fill_mode,
            contours=[points for points in contours if len(points) >= 2],
            landmarks=landmarks,
            source=source,
            notes=str(data.get("notes", "")).strip(),
            description=str(data.get("description", "")).strip(),
            speech=str(data.get("speech", "")).strip(),
            importance=_clamp(data.get("importance"), 0.0, 1.0, 0.0),
            closed=bool(data.get("closed", True)),
            thickness=max(1, min(6, int(_clamp(data.get("thickness"), 1, 6, 1)))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tactile_role": self.tactile_role,
            "boundary_level": self.boundary_level,
            "internal_level": self.internal_level,
            "fill_mode": self.fill_mode,
            "contours": self.contours,
            "landmarks": self.landmarks,
            "source": self.source,
            "notes": self.notes,
            "description": self.description,
            "speech": self.speech,
            "importance": self.importance,
            "closed": self.closed,
            "thickness": self.thickness,
        }


@dataclass
class TactileScene:
    version: int = 1
    content_type: str = "other"
    global_strategy: str = ""
    image_width: int = 0
    image_height: int = 0
    regions: list[TactileRegion] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TactileScene":
        regions = [
            TactileRegion.from_dict(item)
            for item in data.get("regions", [])
            if isinstance(item, dict)
        ]
        width = data.get("image_width", 0)
        height = data.get("image_height", 0)
        return cls(
            version=int(data.get("version", 1)),
            content_type=str(data.get("content_type", "other")),
            global_strategy=str(data.get("global_strategy", "")),
            image_width=int(width) if isinstance(width, (int, float)) else 0,
            image_height=int(height) if isinstance(height, (int, float)) else 0,
            regions=regions,
            relations=[str(item) for item in data.get("relations", [])],
            warnings=[str(item) for item in data.get("warnings", [])],
            metrics=data.get("metrics", {}) if isinstance(data.get("metrics", {}), dict) else {},
            meta=data.get("_meta", {}) if isinstance(data.get("_meta", {}), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "content_type": self.content_type,
            "global_strategy": self.global_strategy,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "regions": [region.to_dict() for region in self.regions],
            "relations": self.relations,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "_meta": self.meta,
        }
