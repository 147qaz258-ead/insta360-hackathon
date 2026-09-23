from __future__ import annotations

import math


def empty_map(width: int = 160, height: int = 96) -> list[list[int]]:
    return [[0 for _ in range(width)] for _ in range(height)]


def make_circle(width: int = 160, height: int = 96) -> list[list[int]]:
    grid = empty_map(width, height)
    cx, cy = width * 0.5, height * 0.5
    radius = min(width, height) * 0.29
    thickness = 1.8
    for y in range(height):
        for x in range(width):
            d = math.hypot(x - cx, y - cy)
            if abs(d - radius) <= thickness:
                grid[y][x] = 1
    return grid


def make_box_diagonal(width: int = 160, height: int = 96) -> list[list[int]]:
    grid = empty_map(width, height)
    x0, x1 = int(width * 0.22), int(width * 0.78)
    y0, y1 = int(height * 0.20), int(height * 0.80)
    for x in range(x0, x1 + 1):
        grid[y0][x] = 1
        grid[y1][x] = 1
    for y in range(y0, y1 + 1):
        grid[y][x0] = 1
        grid[y][x1] = 1
        t = (y - y0) / max(1, y1 - y0)
        x = int(round(x0 + t * (x1 - x0)))
        grid[y][x] = 1
    return grid


def make_two_objects(width: int = 160, height: int = 96) -> list[list[int]]:
    grid = empty_map(width, height)

    # Left rectangle
    lx0, lx1 = int(width * 0.12), int(width * 0.38)
    ly0, ly1 = int(height * 0.28), int(height * 0.72)
    for x in range(lx0, lx1 + 1):
        grid[ly0][x] = 1
        grid[ly1][x] = 1
    for y in range(ly0, ly1 + 1):
        grid[y][lx0] = 1
        grid[y][lx1] = 1

    # Right circle
    cx, cy = width * 0.70, height * 0.50
    radius = min(width, height) * 0.19
    for y in range(height):
        for x in range(width):
            if abs(math.hypot(x - cx, y - cy) - radius) <= 1.7:
                grid[y][x] = 1
    return grid


PATTERNS = {
    "circle": make_circle,
    "box": make_box_diagonal,
    "objects": make_two_objects,
}


def make_pattern(name: str) -> list[list[int]]:
    try:
        return PATTERNS[name]()
    except KeyError as exc:
        raise ValueError(f"unknown synthetic pattern: {name}") from exc
