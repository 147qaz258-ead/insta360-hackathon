from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    screen_px_width: int
    screen_px_height: int
    target_pin_count: int
    aspect_mode: str = "preserve"
    height_levels: int = 2
    grid_align: int = 2

    @property
    def screen_aspect_ratio(self) -> float:
        return self.screen_px_width / self.screen_px_height

    def compute_grid(self) -> tuple[int, int]:
        if self.target_pin_count <= 0:
            raise ValueError("target_pin_count must be positive")
        if self.screen_px_width <= 0 or self.screen_px_height <= 0:
            raise ValueError("screen dimensions must be positive")

        ratio = self.screen_aspect_ratio
        raw_cols = math.sqrt(self.target_pin_count * ratio)
        align = max(1, int(self.grid_align))

        candidates: list[tuple[float, int, int]] = []
        center = max(1, int(round(raw_cols / align)))
        for step in range(max(1, center - 6), center + 7):
            cols = max(align, step * align)
            rows_raw = self.target_pin_count / cols
            rows = max(align, int(round(rows_raw / align)) * align)
            point_error = abs(cols * rows - self.target_pin_count) / self.target_pin_count
            aspect_error = abs((cols / rows) - ratio) / ratio
            score = point_error + 2.5 * aspect_error
            candidates.append((score, cols, rows))

        _, cols, rows = min(candidates, key=lambda item: item[0])
        return cols, rows

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceProfile":
        return cls(
            name=str(data["name"]),
            screen_px_width=int(data["screen_px_width"]),
            screen_px_height=int(data["screen_px_height"]),
            target_pin_count=int(data["target_pin_count"]),
            aspect_mode=str(data.get("aspect_mode", "preserve")),
            height_levels=int(data.get("height_levels", 2)),
            grid_align=int(data.get("grid_align", 2)),
        )

    @classmethod
    def load(cls, path: str | Path) -> "DeviceProfile":
        with Path(path).open("r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
