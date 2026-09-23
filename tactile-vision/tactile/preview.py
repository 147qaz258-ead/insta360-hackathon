from __future__ import annotations

import base64
import struct
import zlib

from tactile.frame import TactileFrame


def render_preview_png(frame: TactileFrame, scale: int = 8) -> bytes:
    """Render the same neutral, top-down 2.5D surface shown by the web page.

    Raised pins are white and fully lowered pins are black, matching the judge-
    facing orthographic digital twin. This binary display colour only exposes
    the physical state; UInt8 height is still represented mechanically by the
    offset/size of the neutral cast shadow.
    """
    rows, cols = frame.rows, frame.cols
    max_level = max(1, frame.levels - 1)
    width = cols * scale
    height = rows * scale
    pixels = [[(48, 55, 57) for _ in range(width)] for _ in range(height)]

    def paint_rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        for py in range(max(0, y0), min(height, y1)):
            row = pixels[py]
            for px in range(max(0, x0), min(width, x1)):
                row[px] = color

    scanlines = bytearray()
    for y in range(rows):
        for x in range(cols):
            level = min(max_level, max(0, int(frame.pins[y * cols + x])))
            normalized = level / max_level
            x0, y0 = x * scale, y * scale
            # Height-dependent neutral shadow, slightly down/right as in the
            # browser renderer. It is a visual cue, never a new pin value.
            offset_x = round(normalized * scale * 1.55)
            offset_y = round(normalized * scale * 1.05)
            if level > 0:
                paint_rect(x0 + 1 + offset_x, y0 + 1 + offset_y,
                           x0 + scale - 1 + offset_x, y0 + scale - 1 + offset_y,
                           (22, 27, 29))
            pin_color = (250, 250, 248) if level > 0 else (4, 5, 5)
            paint_rect(x0 + 1, y0 + 1, x0 + scale - 1, y0 + scale - 1,
                       pin_color)
            if normalized > 0:
                paint_rect(x0, y0, x0 + scale, y0 + 1, (139, 145, 146))
                paint_rect(x0, y0 + scale - 1, x0 + scale, y0 + scale, (139, 145, 146))
        for py in range(y * scale, (y + 1) * scale):
            scanlines.append(0)  # PNG filter: none
            for red, green, blue in pixels[py]:
                scanlines.extend((red, green, blue))

    def chunk(name: bytes, payload: bytes) -> bytes:
        body = name + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + chunk(b"IEND", b"")
    )


def png_to_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
