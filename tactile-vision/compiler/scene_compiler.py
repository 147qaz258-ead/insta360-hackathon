from __future__ import annotations

from typing import Any

from compiler.profiles import DeviceProfile
from scene.tactile_scene import TactileRegion, TactileScene
from tactile.frame import TactileFrame

DEFAULT_IMAGE_WIDTH = 4
DEFAULT_IMAGE_HEIGHT = 3


class TactileSceneCompiler:
    """Deterministic TactileScene -> TactileFrame compiler.

    The agent decides WHAT should be expressed (regions, heights, fill); this
    compiler only decides HOW each pin is set. No semantic or pixel algorithms
    live here, so the same scene compiles identically on every run.
    """

    def __init__(self, sparse_step: int = 2) -> None:
        self.sparse_step = max(1, int(sparse_step))
        self.last_region_map: list[list[str | None]] = []

    def compile(self, scene: TactileScene, profile: DeviceProfile) -> TactileFrame:
        cols, rows = profile.compute_grid()
        image_w = scene.image_width if scene.image_width > 0 else DEFAULT_IMAGE_WIDTH
        image_h = scene.image_height if scene.image_height > 0 else DEFAULT_IMAGE_HEIGHT

        content_x, content_y, content_w, content_h = self._content_box(
            image_w / image_h, cols, rows
        )
        max_level = max(1, profile.height_levels - 1)

        grid = [[0] * cols for _ in range(rows)]
        region_map: list[list[str | None]] = [[None] * cols for _ in range(rows)]

        drawable = [
            region
            for region in scene.regions
            if region.contours and (region.boundary_level > 0 or region.internal_level > 0)
        ]
        drawable.sort(key=lambda region: max(region.boundary_level, region.internal_level))

        for region in drawable:
            self._render_region(
                region,
                grid,
                region_map,
                cols,
                rows,
                content_x,
                content_y,
                content_w,
                content_h,
                max_level,
            )

        pins = [value for row in grid for value in row]
        frame = TactileFrame(
            version=1,
            cols=cols,
            rows=rows,
            levels=profile.height_levels,
            aspect_ratio=cols / rows,
            pins=pins,
            content_box={
                "x": content_x,
                "y": content_y,
                "width": content_w,
                "height": content_h,
            },
            source_size={"width": image_w, "height": image_h},
        )
        self.last_region_map = region_map
        return frame

    def region_hit_map(self) -> dict[str, list[int]]:
        """Flatten the last region map into {region_id: [cell_index, ...]}."""
        hit_map: dict[str, list[int]] = {}
        if not self.last_region_map:
            return hit_map
        cols = len(self.last_region_map[0])
        for y, row in enumerate(self.last_region_map):
            for x, region_id in enumerate(row):
                if region_id:
                    hit_map.setdefault(region_id, []).append(y * cols + x)
        return hit_map

    @staticmethod
    def _content_box(aspect: float, cols: int, rows: int) -> tuple[int, int, int, int]:
        if aspect <= 0:
            aspect = cols / rows
        scale = min(cols / max(aspect, 1e-6), rows) if aspect >= 1 else min(cols, rows * aspect)
        if aspect >= 1:
            content_w = max(1, min(cols, int(round(aspect * scale))))
            content_h = max(1, min(rows, int(round(scale))))
        else:
            content_w = max(1, min(cols, int(round(scale))))
            content_h = max(1, min(rows, int(round(scale / aspect))))
        content_x = (cols - content_w) // 2
        content_y = (rows - content_h) // 2
        return content_x, content_y, content_w, content_h

    def _render_region(
        self,
        region: TactileRegion,
        grid: list[list[int]],
        region_map: list[list[str | None]],
        cols: int,
        rows: int,
        content_x: int,
        content_y: int,
        content_w: int,
        content_h: int,
        max_level: int,
    ) -> None:
        boundary = min(region.boundary_level, max_level)
        internal = min(region.internal_level, max_level)

        def to_grid(point: list[float]) -> tuple[int, int]:
            gx = content_x + point[0] * content_w
            gy = content_y + point[1] * content_h
            return (
                min(cols - 1, max(0, int(round(gx)))),
                min(rows - 1, max(0, int(round(gy)))),
            )

        drawn = 0
        for contour in region.contours:
            points = [to_grid(point) for point in contour]
            if len(points) < 2:
                continue
            if internal > 0 and region.fill_mode in ("solid", "sparse"):
                drawn += self._fill_polygon(
                    grid, region_map, points, region.id, internal, region.fill_mode
                )
            for index in range(len(points)):
                start = points[index]
                end = points[(index + 1) % len(points)]
                drawn += self._draw_line(grid, region_map, start, end, region.id, boundary)

        if drawn == 0 and boundary > 0:
            xs = [p[0] for contour in region.contours for p in contour]
            ys = [p[1] for contour in region.contours for p in contour]
            if xs and ys:
                center = to_grid([sum(xs) / len(xs), sum(ys) / len(ys)])
                grid[center[1]][center[0]] = boundary
                region_map[center[1]][center[0]] = region.id

    @staticmethod
    def _draw_line(
        grid: list[list[int]],
        region_map: list[list[str | None]],
        start: tuple[int, int],
        end: tuple[int, int],
        region_id: str,
        level: int,
    ) -> int:
        if level <= 0:
            return 0
        x0, y0 = start
        x1, y1 = end
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        step_x = 1 if x0 < x1 else -1
        step_y = 1 if y0 < y1 else -1
        error = dx + dy
        rows = len(grid)
        cols = len(grid[0])
        drawn = 0
        while True:
            if grid[y0][x0] < level:
                grid[y0][x0] = level
                region_map[y0][x0] = region_id
                drawn += 1
            if x0 == x1 and y0 == y1:
                break
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x0 += step_x
            if doubled <= dx:
                error += dx
                y0 += step_y
            if not (0 <= x0 < cols and 0 <= y0 < rows):
                break
        return drawn

    def _fill_polygon(
        self,
        grid: list[list[int]],
        region_map: list[list[str | None]],
        points: list[tuple[int, int]],
        region_id: str,
        level: int,
        fill_mode: str,
    ) -> int:
        if level <= 0 or len(points) < 3:
            return 0
        rows = len(grid)
        cols = len(grid[0])
        ys = [point[1] for point in points]
        drawn = 0
        step = self.sparse_step if fill_mode == "sparse" else 1
        for gy in range(max(0, min(ys)), min(rows - 1, max(ys)) + 1):
            center_y = gy + 0.5
            crossings: list[float] = []
            for index in range(len(points)):
                x0, y0 = points[index]
                x1, y1 = points[(index + 1) % len(points)]
                if y0 == y1:
                    continue
                if (y0 <= center_y < y1) or (y1 <= center_y < y0):
                    crossings.append(x0 + (center_y - y0) * (x1 - x0) / (y1 - y0))
            crossings.sort()
            for index in range(0, len(crossings) - 1, 2):
                start_x = int(round(crossings[index]))
                end_x = int(round(crossings[index + 1]))
                for gx in range(max(0, start_x), min(cols - 1, end_x) + 1):
                    if step > 1 and (gx + gy) % step != 0:
                        continue
                    if grid[gy][gx] < level:
                        grid[gy][gx] = level
                        region_map[gy][gx] = region_id
                        drawn += 1
        return drawn

    def summarize(self, frame: TactileFrame) -> dict[str, Any]:
        level_counts: dict[str, int] = {}
        for pin in frame.pins:
            level_counts[str(pin)] = level_counts.get(str(pin), 0) + 1
        active = frame.cols * frame.rows - level_counts.get("0", 0)
        return {
            "grid": f"{frame.cols}x{frame.rows}",
            "levels": frame.levels,
            "active_pins": active,
            "active_pin_ratio": round(active / len(frame.pins), 4),
            "level_counts": level_counts,
        }
