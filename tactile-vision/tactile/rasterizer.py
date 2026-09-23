from __future__ import annotations

from collections.abc import Sequence

from compiler.profiles import DeviceProfile
from tactile.frame import TactileFrame


BinaryMap = Sequence[Sequence[int | bool]]


class AdaptiveRasterizer:
    """Fit a binary structural map into a device grid without crop or stretch."""

    def rasterize(self, structural_map: BinaryMap, profile: DeviceProfile) -> TactileFrame:
        src = self._normalize_map(structural_map)
        src_h = len(src)
        src_w = len(src[0])
        cols, rows = profile.compute_grid()

        scale = min(cols / src_w, rows / src_h)
        fit_w = max(1, min(cols, int(round(src_w * scale))))
        fit_h = max(1, min(rows, int(round(src_h * scale))))
        offset_x = (cols - fit_w) // 2
        offset_y = (rows - fit_h) // 2

        out = [[0 for _ in range(cols)] for _ in range(rows)]

        if fit_w < src_w or fit_h < src_h:
            fitted = self._coverage_downsample(src, fit_w, fit_h)
        else:
            fitted = self._nearest_resize(src, fit_w, fit_h)

        for dy, fitted_row in enumerate(fitted):
            for dx, value in enumerate(fitted_row):
                out[offset_y + dy][offset_x + dx] = value

        pins = [value for row in out for value in row]
        return TactileFrame(
            version=1,
            cols=cols,
            rows=rows,
            levels=profile.height_levels,
            aspect_ratio=cols / rows,
            pins=pins,
            content_box={
                "x": offset_x,
                "y": offset_y,
                "width": fit_w,
                "height": fit_h,
            },
            source_size={"width": src_w, "height": src_h},
        )

    @staticmethod
    def _normalize_map(structural_map: BinaryMap) -> list[list[int]]:
        rows = [list(row) for row in structural_map]
        if not rows or not rows[0]:
            raise ValueError("structural_map cannot be empty")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("structural_map must be rectangular")
        return [[1 if value else 0 for value in row] for row in rows]

    @staticmethod
    def _nearest_resize(src: list[list[int]], width: int, height: int) -> list[list[int]]:
        src_h = len(src)
        src_w = len(src[0])
        out = [[0 for _ in range(width)] for _ in range(height)]
        for dy in range(height):
            sy = min(src_h - 1, int((dy + 0.5) * src_h / height))
            for dx in range(width):
                sx = min(src_w - 1, int((dx + 0.5) * src_w / width))
                out[dy][dx] = src[sy][sx]
        return out

    @staticmethod
    def _coverage_downsample(
        src: list[list[int]], width: int, height: int
    ) -> list[list[int]]:
        """Preserve thin structural lines by testing the full source footprint.

        Centre-point nearest-neighbour sampling can completely miss a one-pixel
        line. Structural maps value coverage over colour averaging, so a target
        cell becomes active when any source structure overlaps its footprint.
        """

        src_h = len(src)
        src_w = len(src[0])
        out = [[0 for _ in range(width)] for _ in range(height)]
        for dy in range(height):
            y0 = (dy * src_h) // height
            y1 = max(y0 + 1, ((dy + 1) * src_h + height - 1) // height)
            y1 = min(src_h, y1)
            for dx in range(width):
                x0 = (dx * src_w) // width
                x1 = max(x0 + 1, ((dx + 1) * src_w + width - 1) // width)
                x1 = min(src_w, x1)
                if any(src[sy][sx] for sy in range(y0, y1) for sx in range(x0, x1)):
                    out[dy][dx] = 1
        return out
