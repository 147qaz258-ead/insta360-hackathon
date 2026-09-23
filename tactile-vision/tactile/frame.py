from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


@dataclass
class TactileFrame:
    version: int
    cols: int
    rows: int
    levels: int
    aspect_ratio: float
    pins: list[int]
    content_box: dict[str, int] | None = None
    source_size: dict[str, int] | None = None
    frame_id: int = 0

    def __post_init__(self) -> None:
        if self.cols <= 0 or self.rows <= 0:
            raise ValueError("cols and rows must be positive")
        if self.levels < 2:
            raise ValueError("levels must be >= 2")
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if self.frame_id < 0 or self.frame_id > 0xFFFFFFFF:
            raise ValueError("frame_id must fit uint32")
        expected = self.cols * self.rows
        if len(self.pins) != expected:
            raise ValueError(f"pins length {len(self.pins)} != cols*rows {expected}")
        max_level = self.levels - 1
        if any((pin < 0 or pin > max_level) for pin in self.pins):
            raise ValueError("pin value outside configured height levels")

    def at(self, x: int, y: int) -> int:
        return self.pins[y * self.cols + x]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_rows(
        cls,
        rows_data: Iterable[Iterable[int]],
        levels: int = 2,
        version: int = 1,
    ) -> "TactileFrame":
        matrix = [list(map(int, row)) for row in rows_data]
        if not matrix or not matrix[0]:
            raise ValueError("rows_data cannot be empty")
        cols = len(matrix[0])
        if any(len(row) != cols for row in matrix):
            raise ValueError("rows_data must be rectangular")
        rows = len(matrix)
        pins = [value for row in matrix for value in row]
        return cls(
            version=version,
            cols=cols,
            rows=rows,
            levels=levels,
            aspect_ratio=cols / rows,
            pins=pins,
        )
