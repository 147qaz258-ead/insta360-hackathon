from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence


GrayMap = Sequence[Sequence[int]]
BinaryMap = list[list[int]]


@dataclass(frozen=True)
class BaselineConfig:
    max_input_dimension: int = 640
    edge_quantile: float = 0.72
    minimum_gradient: int = 72
    close_iterations: int = 1
    minimum_component_pixels: int = 6

    def __post_init__(self) -> None:
        if self.max_input_dimension <= 0:
            raise ValueError("max_input_dimension must be positive")
        if not 0.0 < self.edge_quantile < 1.0:
            raise ValueError("edge_quantile must be between 0 and 1")
        if self.minimum_gradient < 0:
            raise ValueError("minimum_gradient cannot be negative")


def load_grayscale(path: str | Path, max_dimension: int = 640) -> list[list[int]]:
    """Load the full image and scale it down without crop or aspect distortion."""

    image_path = Path(path)
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    try:
        import cv2  # type: ignore

        image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(f"cannot decode image: {image_path}")
        height, width = image.shape
        scale = min(1.0, max_dimension / max(width, height))
        if scale < 1.0:
            image = cv2.resize(
                image,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        image = cv2.GaussianBlur(image, (3, 3), 0)
        return image.tolist()
    except ImportError:
        try:
            from PIL import Image, ImageFilter
        except ImportError as exc:
            raise RuntimeError("image input requires OpenCV or Pillow") from exc

        with Image.open(image_path) as source:
            image = source.convert("L")
            width, height = image.size
            scale = min(1.0, max_dimension / max(width, height))
            if scale < 1.0:
                image = image.resize(
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    Image.Resampling.LANCZOS,
                )
            image = image.filter(ImageFilter.GaussianBlur(radius=0.8))
            width, height = image.size
            pixels = list(image.getdata())
        return [pixels[y * width : (y + 1) * width] for y in range(height)]


def extract_structural_map(
    grayscale: GrayMap, config: BaselineConfig | None = None
) -> BinaryMap:
    """Extract a deterministic Sobel structural baseline without semantic removal."""

    config = config or BaselineConfig()
    gray = _validate_grayscale(grayscale)
    height = len(gray)
    width = len(gray[0])
    magnitude = [[0 for _ in range(width)] for _ in range(height)]
    nonzero: list[int] = []

    for y in range(1, height - 1):
        upper = gray[y - 1]
        middle = gray[y]
        lower = gray[y + 1]
        for x in range(1, width - 1):
            gx = (
                -upper[x - 1]
                + upper[x + 1]
                - 2 * middle[x - 1]
                + 2 * middle[x + 1]
                - lower[x - 1]
                + lower[x + 1]
            )
            gy = (
                -upper[x - 1]
                - 2 * upper[x]
                - upper[x + 1]
                + lower[x - 1]
                + 2 * lower[x]
                + lower[x + 1]
            )
            value = abs(gx) + abs(gy)
            magnitude[y][x] = value
            if value:
                nonzero.append(value)

    if not nonzero:
        return [[0 for _ in range(width)] for _ in range(height)]

    nonzero.sort()
    quantile_index = min(
        len(nonzero) - 1, max(0, int(round((len(nonzero) - 1) * config.edge_quantile)))
    )
    threshold = max(config.minimum_gradient, nonzero[quantile_index])
    binary = [
        [1 if magnitude[y][x] >= threshold else 0 for x in range(width)]
        for y in range(height)
    ]

    for _ in range(max(0, config.close_iterations)):
        binary = _erode(_dilate(binary))
    if config.minimum_component_pixels > 1:
        binary = _remove_small_components(binary, config.minimum_component_pixels)
    return binary


def save_binary_map(structural_map: Sequence[Sequence[int]], path: str | Path) -> None:
    rows = [[255 if value else 0 for value in row] for row in structural_map]
    if not rows or not rows[0]:
        raise ValueError("structural_map cannot be empty")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image

        height = len(rows)
        width = len(rows[0])
        image = Image.new("L", (width, height))
        image.putdata([value for row in rows for value in row])
        image.save(output_path)
    except ImportError:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError as exc:
            raise RuntimeError("debug image output requires Pillow or OpenCV") from exc
        cv2.imwrite(str(output_path), np.asarray(rows, dtype=np.uint8))


def _validate_grayscale(grayscale: GrayMap) -> list[list[int]]:
    rows = [list(map(int, row)) for row in grayscale]
    if len(rows) < 3 or len(rows[0]) < 3:
        raise ValueError("grayscale image must be at least 3x3")
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("grayscale image must be rectangular")
    if any(value < 0 or value > 255 for row in rows for value in row):
        raise ValueError("grayscale values must be in range 0..255")
    return rows


def _dilate(binary: BinaryMap) -> BinaryMap:
    height = len(binary)
    width = len(binary[0])
    out = [[0 for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            if any(
                binary[ny][nx]
                for ny in range(max(0, y - 1), min(height, y + 2))
                for nx in range(max(0, x - 1), min(width, x + 2))
            ):
                out[y][x] = 1
    return out


def _erode(binary: BinaryMap) -> BinaryMap:
    height = len(binary)
    width = len(binary[0])
    out = [[0 for _ in range(width)] for _ in range(height)]
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            if all(binary[ny][nx] for ny in range(y - 1, y + 2) for nx in range(x - 1, x + 2)):
                out[y][x] = 1
    return out


def _remove_small_components(binary: BinaryMap, minimum_pixels: int) -> BinaryMap:
    height = len(binary)
    width = len(binary[0])
    out = [row[:] for row in binary]
    visited: set[tuple[int, int]] = set()

    for y in range(height):
        for x in range(width):
            if not out[y][x] or (x, y) in visited:
                continue
            stack = [(x, y)]
            visited.add((x, y))
            component: list[tuple[int, int]] = []
            while stack:
                cx, cy = stack.pop()
                component.append((cx, cy))
                for ny in range(max(0, cy - 1), min(height, cy + 2)):
                    for nx in range(max(0, cx - 1), min(width, cx + 2)):
                        if out[ny][nx] and (nx, ny) not in visited:
                            visited.add((nx, ny))
                            stack.append((nx, ny))
            if len(component) < minimum_pixels:
                for cx, cy in component:
                    out[cy][cx] = 0
    return out
