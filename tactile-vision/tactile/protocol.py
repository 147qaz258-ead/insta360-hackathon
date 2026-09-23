from __future__ import annotations

import struct
import zlib

from tactile.frame import TactileFrame


MAGIC_V1 = b"TCF1"
MAGIC_V2 = b"TCF2"
FLAG_BINARY_PACKED = 0x01
FLAG_UINT8_HEIGHTS = 0x02
HEADER = struct.Struct(">4sBBHHBII")
CRC = struct.Struct(">I")


class FrameProtocolError(ValueError):
    pass


def _pack_binary_pins(pins: list[int]) -> bytes:
    payload = bytearray((len(pins) + 7) // 8)
    for index, value in enumerate(pins):
        if value:
            payload[index // 8] |= 1 << (index % 8)
    return bytes(payload)


def _unpack_binary_pins(payload: bytes, count: int) -> list[int]:
    return [
        1 if payload[index // 8] & (1 << (index % 8)) else 0
        for index in range(count)
    ]


def encode_frame(frame: TactileFrame) -> bytes:
    if frame.levels == 2:
        magic = MAGIC_V1
        flags = FLAG_BINARY_PACKED
        payload = _pack_binary_pins(frame.pins)
    elif frame.levels == 256:
        magic = MAGIC_V2
        flags = FLAG_UINT8_HEIGHTS
        payload = bytes(frame.pins)
    else:
        raise FrameProtocolError("wire format supports binary or UInt8 height only")
    if frame.version > 0xFF:
        raise FrameProtocolError("frame version must fit uint8")
    if frame.frame_id < 0 or frame.frame_id > 0xFFFFFFFF:
        raise FrameProtocolError("frame_id must fit uint32")
    if frame.cols > 0xFFFF or frame.rows > 0xFFFF:
        raise FrameProtocolError("frame dimensions must fit uint16")

    header = HEADER.pack(
        magic,
        frame.version,
        flags,
        frame.cols,
        frame.rows,
        0 if frame.levels == 256 else frame.levels,
        frame.frame_id,
        len(payload),
    )
    body = header + payload
    return body + CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)


def decode_frame(data: bytes) -> TactileFrame:
    minimum = HEADER.size + CRC.size
    if len(data) < minimum:
        raise FrameProtocolError("frame is shorter than protocol header")

    header = data[: HEADER.size]
    magic, version, flags, cols, rows, levels, frame_id, payload_size = HEADER.unpack(
        header
    )
    if magic not in {MAGIC_V1, MAGIC_V2}:
        raise FrameProtocolError("invalid frame magic")
    if magic == MAGIC_V1 and (flags != FLAG_BINARY_PACKED or levels != 2):
        raise FrameProtocolError("invalid binary frame flags or levels")
    if magic == MAGIC_V2 and (flags != FLAG_UINT8_HEIGHTS or levels != 0):
        raise FrameProtocolError("invalid UInt8 frame flags or levels")

    expected_size = HEADER.size + payload_size + CRC.size
    if len(data) != expected_size:
        raise FrameProtocolError("payload length does not match header")

    expected_crc = CRC.unpack(data[-CRC.size :])[0]
    actual_crc = zlib.crc32(data[:-CRC.size]) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise FrameProtocolError("CRC32 mismatch")

    pin_count = cols * rows
    expected_payload_size = (pin_count + 7) // 8 if flags == FLAG_BINARY_PACKED else pin_count
    if payload_size != expected_payload_size:
        raise FrameProtocolError("packed payload size does not match grid")

    payload = data[HEADER.size : -CRC.size]
    pins = (
        _unpack_binary_pins(payload, pin_count)
        if flags == FLAG_BINARY_PACKED
        else list(payload)
    )
    return TactileFrame(
        version=version,
        cols=cols,
        rows=rows,
        levels=256 if magic == MAGIC_V2 else levels,
        aspect_ratio=cols / rows,
        pins=pins,
        frame_id=frame_id,
    )
