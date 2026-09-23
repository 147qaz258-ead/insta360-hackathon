"""Equirectangular 360° panorama -> perspective view renderer.

Coordinate convention:
- yaw=0, pitch=0 looks at the horizontal center of the equirect image.
- yaw increases to the right (clockwise seen from above), range [-180, 180).
- pitch increases upward, range [-90, 90].
- roll rotates the output image clockwise around the view axis.
- fov is the HORIZONTAL field of view in degrees; vertical fov follows aspect.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np


def imread_unicode(path: Path) -> np.ndarray | None:
    """cv2.imread 在 Windows 不支持非 ASCII 路径，用 imdecode 代替。"""
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_unicode(path: Path, img: np.ndarray, quality: int = 92) -> bool:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return False
    buf.tofile(str(path))
    return True


@dataclass
class ViewSpec:
    view_id: str
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    fov: float = 90.0
    width: int = 1280
    height: int = 720

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height


def _rotation_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)

    # yaw: rotation around world Y (up) axis; positive yaw turns right
    Ry = np.array([
        [math.cos(yaw), 0.0, math.sin(yaw)],
        [0.0, 1.0, 0.0],
        [-math.sin(yaw), 0.0, math.cos(yaw)],
    ])
    # pitch: rotation around camera X axis; positive pitch looks up
    Rx = np.array([
        [1.0, 0.0, 0.0],
        [0.0, math.cos(pitch), math.sin(pitch)],
        [0.0, -math.sin(pitch), math.cos(pitch)],
    ])
    # roll: rotation around camera Z (view) axis
    Rz = np.array([
        [math.cos(roll), -math.sin(roll), 0.0],
        [math.sin(roll), math.cos(roll), 0.0],
        [0.0, 0.0, 1.0],
    ])
    return Ry @ Rx @ Rz


def render_view(pano: np.ndarray, spec: ViewSpec) -> np.ndarray:
    """Render a perspective view from an equirectangular panorama (BGR)."""
    eq_h, eq_w = pano.shape[:2]
    out_w, out_h = spec.width, spec.height

    fov_x = math.radians(spec.fov)
    tan_half_x = math.tan(fov_x / 2.0)
    tan_half_y = tan_half_x * out_h / out_w

    # pixel grid -> camera ray directions
    xs = (2.0 * (np.arange(out_w, dtype=np.float32) + 0.5) / out_w - 1.0) * tan_half_x
    ys = (1.0 - 2.0 * (np.arange(out_h, dtype=np.float32) + 0.5) / out_h) * tan_half_y
    gx, gy = np.meshgrid(xs, ys)
    gz = np.ones_like(gx)

    # normalize rays
    norm = np.sqrt(gx * gx + gy * gy + gz * gz)
    dirs = np.stack([gx / norm, gy / norm, gz / norm], axis=-1).reshape(-1, 3)

    # rotate into world space
    R = _rotation_matrix(spec.yaw, spec.pitch, spec.roll)
    world = dirs @ R.T

    dx, dy, dz = world[:, 0], world[:, 1], world[:, 2]
    lon = np.arctan2(dx, dz)                       # [-pi, pi]
    lat = np.arcsin(np.clip(dy, -1.0, 1.0))        # [-pi/2, pi/2]

    map_x = ((lon / (2.0 * math.pi)) + 0.5) * eq_w
    map_y = (0.5 - lat / math.pi) * eq_h
    map_x = map_x.reshape(out_h, out_w).astype(np.float32)
    map_y = map_y.reshape(out_h, out_w).astype(np.float32)

    # horizontal wraparound for seamless sampling at the seam
    map_x = np.mod(map_x, eq_w)
    return cv2.remap(pano, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


class PanoramaViewGenerator:
    """Loads a panorama, renders overview grids and arbitrary views, saves metadata."""

    def __init__(self, panorama_path: str | Path):
        self.panorama_path = Path(panorama_path)
        pano = imread_unicode(self.panorama_path)
        if pano is None:
            raise ValueError(f"Cannot read panorama: {self.panorama_path}")
        self.pano = pano
        self.pano_width, self.pano_height = pano.shape[1], pano.shape[0]

    @staticmethod
    def default_overview_specs(width: int = 1280, height: int = 720, fov: float = 100.0) -> list[ViewSpec]:
        specs: list[ViewSpec] = []
        for i, yaw in enumerate(range(0, 360, 60)):
            specs.append(ViewSpec(view_id=f"view_{i+1:02d}", yaw=float(yaw), pitch=0.0, fov=fov, width=width, height=height))
        specs.append(ViewSpec(view_id=f"view_{len(specs)+1:02d}", yaw=0.0, pitch=60.0, fov=fov, width=width, height=height))
        specs.append(ViewSpec(view_id=f"view_{len(specs)+1:02d}", yaw=0.0, pitch=-60.0, fov=fov, width=width, height=height))
        return specs

    def render_to_file(self, spec: ViewSpec, out_dir: str | Path) -> Path:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        img = render_view(self.pano, spec)
        path = out_dir / f"{spec.view_id}.jpg"
        imwrite_unicode(path, img)
        return path

    def generate_overview(self, out_dir: str | Path) -> list[dict]:
        out_dir = Path(out_dir)
        specs = self.default_overview_specs()
        entries = []
        for spec in specs:
            path = self.render_to_file(spec, out_dir)
            entry = asdict(spec)
            entry["path"] = str(path)
            entries.append(entry)
        (out_dir / "views.json").write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return entries
