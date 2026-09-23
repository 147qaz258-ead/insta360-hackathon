from __future__ import annotations

from typing import Any

from scene.models import GroundedScene, GroundedRegion, SceneUnderstanding, TactilePlan
from scene.tactile_scene import TactileRegion, TactileScene


ROLE_DEFAULT_LEVELS = {
    "primary": (3, 2),
    "secondary": (2, 1),
    "supporting": (1, 0),
    "text_marker": (2, 0),
    "geometry": (2, 1),
    "background_structure": (1, 0),
}

MIN_BOX_SPAN = 0.01


def _bbox_to_contour(bbox: list[float]) -> list[list[float]] | None:
    """Convert a normalized [x1, y1, x2, y2] box into a closed polygon contour."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None

    # Accept both 0-1000 (Qwen convention) and 0-1 inputs.
    if max(x1, y1, x2, y2) > 1.0:
        x1, y1, x2, y2 = (v / 1000.0 for v in (x1, y1, x2, y2))

    x1 = min(1.0, max(0.0, x1))
    x2 = min(1.0, max(0.0, x2))
    y1 = min(1.0, max(0.0, y1))
    y2 = min(1.0, max(0.0, y2))
    if x2 - x1 < MIN_BOX_SPAN or y2 - y1 < MIN_BOX_SPAN:
        return None
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


class TactileSceneBuilder:
    """Turn the agent's TactilePlan into a device-independent TactileScene.

    Geometry comes from the model itself (approx_bbox written by the VLM) or,
    once available, from a GroundedScene produced by precise grounding. No
    heuristic position guessing happens here: a region without usable geometry
    is reported as unplaced instead of being invented.
    """

    def build(
        self,
        scene_understanding: SceneUnderstanding,
        plan: TactilePlan,
        grounded_scene: GroundedScene | None = None,
    ) -> TactileScene:
        grounded_by_id = self._grounded_index(grounded_scene)
        grounded_size = (
            (grounded_scene.image_width, grounded_scene.image_height)
            if grounded_scene is not None
            else (0, 0)
        )
        regions: list[TactileRegion] = []
        warnings: list[str] = list(plan.warnings)
        unplaced: list[str] = []
        source_counts = {"grounded": 0, "approximate": 0, "unplaced": 0}

        for plan_region in plan.regions:
            region_id = str(plan_region.get("id", "")).strip()
            if not region_id:
                continue
            scene_region = self._find_scene_region(scene_understanding, region_id)
            name = str(plan_region.get("name", "")).strip() or (
                scene_region.name if scene_region else region_id
            )
            role = str(plan_region.get("tactile_role", "")).strip() or "secondary"
            default_boundary, default_internal = ROLE_DEFAULT_LEVELS.get(role, (2, 1))
            boundary_level = self._level(plan_region.get("boundary_level"), default_boundary)
            internal_level = self._level(plan_region.get("internal_level"), default_internal)

            fill_mode = str(plan_region.get("fill_mode", "none")).strip().casefold()
            if fill_mode not in {"none", "sparse", "solid"}:
                fill_mode = "none"

            contour, source = self._resolve_geometry(
                region_id, plan_region, grounded_by_id, grounded_size
            )
            if contour is None:
                source = "unplaced"
                unplaced.append(region_id)
                warnings.append(
                    f"region {region_id} 没有可用几何（等待精确 grounding），本轮未编译"
                )
            elif boundary_level == 0 and internal_level == 0:
                source = "unplaced"

            if source in source_counts:
                source_counts[source] += 1

            regions.append(
                TactileRegion(
                    id=region_id,
                    name=name,
                    tactile_role=role,
                    boundary_level=boundary_level,
                    internal_level=internal_level,
                    fill_mode=fill_mode,
                    contours=[contour] if contour is not None else [],
                    source=source,
                    notes=str(plan_region.get("separation_notes", "")).strip(),
                )
            )

        relations = list(plan.preserve_relations)
        if not relations:
            relations = [
                f"{relation.subject} {relation.relation} {relation.object}"
                for relation in scene_understanding.relations
            ]

        planned = len(regions)
        rendered = sum(1 for region in regions if region.source != "unplaced")
        scene = TactileScene(
            content_type=scene_understanding.content_type,
            global_strategy=plan.global_strategy,
            regions=regions,
            relations=relations,
            warnings=warnings,
            metrics={
                "planned_regions": planned,
                "rendered_regions": rendered,
                "unplaced_regions": len(unplaced),
                "unplaced_ids": unplaced,
                "geometry_sources": source_counts,
            },
            meta={"compression_rules": plan.compression_rules},
        )
        return scene

    @staticmethod
    def _grounded_index(
        grounded_scene: GroundedScene | None,
    ) -> dict[str, GroundedRegion]:
        if grounded_scene is None:
            return {}
        index: dict[str, GroundedRegion] = {}
        for region in grounded_scene.regions:
            if region.bbox_xyxy and region.id:
                index.setdefault(region.id, region)
        return index

    @staticmethod
    def _find_scene_region(
        scene_understanding: SceneUnderstanding, region_id: str
    ) -> Any:
        for region in scene_understanding.regions:
            if region.id == region_id:
                return region
        return None

    def _resolve_geometry(
        self,
        region_id: str,
        plan_region: dict[str, Any],
        grounded_by_id: dict[str, GroundedRegion],
        grounded_size: tuple[int, int],
    ) -> tuple[list[list[float]] | None, str]:
        grounded = grounded_by_id.get(region_id)
        if grounded is not None:
            contour = self._grounded_contour(grounded, grounded_size)
            if contour is not None:
                return contour, "grounded"

        contour = _bbox_to_contour(plan_region.get("approx_bbox"))
        if contour is not None:
            return contour, "approximate"
        return None, "unplaced"

    @staticmethod
    def _grounded_contour(
        region: GroundedRegion, image_size: tuple[int, int]
    ) -> list[list[float]] | None:
        bbox = region.bbox_xyxy
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        try:
            x1, y1, x2, y2 = (float(v) for v in bbox)
        except (TypeError, ValueError):
            return None
        width, height = image_size
        if width <= 0 or height <= 0:
            # Grounding stored normalized coordinates instead of pixels.
            if max(x1, y1, x2, y2) > 1.0:
                x1, y1, x2, y2 = (v / 1000.0 for v in (x1, y1, x2, y2))
        else:
            x1, x2 = x1 / width, x2 / width
            y1, y2 = y1 / height, y2 / height
        x1 = min(1.0, max(0.0, x1))
        x2 = min(1.0, max(0.0, x2))
        y1 = min(1.0, max(0.0, y1))
        y2 = min(1.0, max(0.0, y2))
        if x2 - x1 < MIN_BOX_SPAN or y2 - y1 < MIN_BOX_SPAN:
            return None
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    @staticmethod
    def _level(value: Any, default: int) -> int:
        try:
            if value is None:
                return default
            number = int(round(float(value)))
        except (TypeError, ValueError):
            return default
        return max(0, min(3, number))
