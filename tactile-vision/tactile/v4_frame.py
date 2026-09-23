from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


FRAME_VERSION = 2
DEVICE_ID = "rdk_hdmi"
COLS = 80
ROWS = 48
PIN_COUNT = COLS * ROWS
HEIGHT_ENCODING = "uint8"


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str
    expected: Any | None = None
    actual: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


def _issue(
    errors: list[ValidationIssue],
    path: str,
    code: str,
    message: str,
    expected: Any | None = None,
    actual: Any | None = None,
) -> None:
    errors.append(ValidationIssue(path, code, message, expected, actual))


def _validate_matrix(
    value: Any,
    field: str,
    errors: list[ValidationIssue],
    *,
    minimum: int,
    maximum: int | None,
) -> list[list[int]] | None:
    if not isinstance(value, list):
        _issue(errors, field, "type", f"{field} 必须是二维数组", "array[48][80]", type(value).__name__)
        return None
    if len(value) != ROWS:
        _issue(errors, field, "row_count", f"{field} 必须正好包含 {ROWS} 行", ROWS, len(value))

    normalized: list[list[int]] = []
    for row_index, row in enumerate(value):
        row_path = f"{field}[{row_index}]"
        if not isinstance(row, list):
            _issue(errors, row_path, "type", "该行必须是数组", f"array[{COLS}]", type(row).__name__)
            continue
        if len(row) != COLS:
            _issue(errors, row_path, "column_count", f"该行必须正好包含 {COLS} 个值", COLS, len(row))
        normalized_row: list[int] = []
        for col_index, raw in enumerate(row):
            path = f"{row_path}[{col_index}]"
            if isinstance(raw, bool) or not isinstance(raw, int):
                _issue(errors, path, "integer", "点位值必须是整数", "integer", raw)
                continue
            if raw < minimum or (maximum is not None and raw > maximum):
                expected = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
                _issue(errors, path, "range", f"点位值必须位于 {expected}", expected, raw)
            normalized_row.append(raw)
        normalized.append(normalized_row)
    return normalized


def _decode_rle_matrix(
    value: Any,
    field: str,
    errors: list[ValidationIssue],
    *,
    minimum: int,
    maximum: int | None,
) -> list[list[int]] | None:
    if not isinstance(value, list):
        _issue(errors, field, "type", f"{field} 必须是逐行 RLE 数组", "array[48]", type(value).__name__)
        return None
    if len(value) != ROWS:
        _issue(errors, field, "row_count", f"{field} 必须正好包含 {ROWS} 行", ROWS, len(value))
    decoded: list[list[int]] = []
    for row_index, runs in enumerate(value):
        row_path = f"{field}[{row_index}]"
        if not isinstance(runs, list):
            _issue(errors, row_path, "type", "RLE 行必须是 [count,value] 数组", "array", type(runs).__name__)
            continue
        row: list[int] = []
        for run_index, run in enumerate(runs):
            path = f"{row_path}[{run_index}]"
            if not isinstance(run, list) or len(run) != 2:
                _issue(errors, path, "rle_pair", "每个 RLE 段必须是 [count,value]", "[count,value]", run)
                continue
            count, raw = run
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                _issue(errors, f"{path}[0]", "rle_count", "RLE count 必须是正整数", ">0 integer", count)
                continue
            if isinstance(raw, bool) or not isinstance(raw, int):
                _issue(errors, f"{path}[1]", "integer", "RLE value 必须是整数", "integer", raw)
                continue
            if raw < minimum or (maximum is not None and raw > maximum):
                expected = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
                _issue(errors, f"{path}[1]", "range", f"RLE value 必须位于 {expected}", expected, raw)
            row.extend([raw] * count)
        if len(row) != COLS:
            _issue(errors, row_path, "decoded_column_count", "RLE 行展开后必须正好是 80 个点", COLS, len(row))
        decoded.append(row)
    return decoded


def _candidate_matrix(
    candidate: dict[str, Any],
    field: str,
    errors: list[ValidationIssue],
    *,
    minimum: int,
    maximum: int | None,
) -> list[list[int]] | None:
    if field in candidate:
        return _validate_matrix(candidate.get(field), field, errors, minimum=minimum, maximum=maximum)
    rle_field = field + "_rle"
    return _decode_rle_matrix(candidate.get(rle_field), rle_field, errors, minimum=minimum, maximum=maximum)


def validate_tactile_candidate(candidate: Any) -> ValidationReport:
    """Validate the hardware contract without repairing or interpreting it."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    if not isinstance(candidate, dict):
        _issue(errors, "$", "type", "候选帧必须是 JSON object", "object", type(candidate).__name__)
        return ValidationReport(False, errors, warnings)

    required_scalars = {
        "version": FRAME_VERSION,
        "device_id": DEVICE_ID,
        "cols": COLS,
        "rows": ROWS,
        "height_encoding": HEIGHT_ENCODING,
    }
    for field, expected in required_scalars.items():
        actual = candidate.get(field)
        if actual != expected:
            _issue(errors, field, "contract", f"{field} 与硬件契约不一致", expected, actual)

    height_rows = _candidate_matrix(
        candidate, "height_rows", errors, minimum=0, maximum=255
    )
    region_rows = _candidate_matrix(
        candidate, "region_rows", errors, minimum=0, maximum=None
    )

    regions_value = candidate.get("regions")
    region_ids: set[int] = set()
    if not isinstance(regions_value, list):
        _issue(errors, "regions", "type", "regions 必须是数组", "array", type(regions_value).__name__)
        regions_value = []
    for index, region in enumerate(regions_value):
        path = f"regions[{index}]"
        if not isinstance(region, dict):
            _issue(errors, path, "type", "区域必须是 object", "object", type(region).__name__)
            continue
        region_id = region.get("id")
        if isinstance(region_id, bool) or not isinstance(region_id, int) or region_id <= 0:
            _issue(errors, f"{path}.id", "region_id", "区域 id 必须是大于 0 的整数", ">0 integer", region_id)
        elif region_id in region_ids:
            _issue(errors, f"{path}.id", "duplicate", "区域 id 重复", "unique", region_id)
        else:
            region_ids.add(region_id)
        for field in ("name", "description", "speech"):
            if not isinstance(region.get(field), str) or not region.get(field, "").strip():
                _issue(errors, f"{path}.{field}", "required_text", f"{field} 必须是非空文本", "non-empty string", region.get(field))

    for field in ("scene_summary", "audio_overview"):
        if not isinstance(candidate.get(field), str) or not candidate.get(field, "").strip():
            _issue(errors, field, "required_text", f"{field} 必须是非空文本", "non-empty string", candidate.get(field))

    if height_rows is not None and region_rows is not None:
        comparable_rows = min(len(height_rows), len(region_rows), ROWS)
        for row_index in range(comparable_rows):
            comparable_cols = min(len(height_rows[row_index]), len(region_rows[row_index]), COLS)
            for col_index in range(comparable_cols):
                height = height_rows[row_index][col_index]
                region_id = region_rows[row_index][col_index]
                if height > 0 and region_id == 0:
                    _issue(
                        errors,
                        f"region_rows[{row_index}][{col_index}]",
                        "unowned_raised_pin",
                        "升起点必须属于模型定义的语义区域",
                        "positive region id",
                        0,
                    )
                elif region_id > 0 and region_id not in region_ids:
                    _issue(
                        errors,
                        f"region_rows[{row_index}][{col_index}]",
                        "unknown_region",
                        "点位引用了 regions 中不存在的区域 id",
                        sorted(region_ids),
                        region_id,
                    )
                elif height == 0 and region_id > 0:
                    warnings.append(ValidationIssue(
                        f"region_rows[{row_index}][{col_index}]",
                        "region_on_lowered_pin",
                        "落下点带有区域 id；允许发布，但该点不会触发凸点触摸",
                        0,
                        region_id,
                    ))

    return ValidationReport(not errors, errors, warnings)


def materialize_tactile_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Losslessly expand transport RLE into the canonical 48x80 matrices."""
    errors: list[ValidationIssue] = []
    height_rows = _candidate_matrix(candidate, "height_rows", errors, minimum=0, maximum=255)
    region_rows = _candidate_matrix(candidate, "region_rows", errors, minimum=0, maximum=None)
    if errors or height_rows is None or region_rows is None:
        first = errors[0] if errors else ValidationIssue("$", "decode", "矩阵无法展开")
        raise ValueError(f"{first.path}: {first.message}")
    result = dict(candidate)
    result["height_rows"] = height_rows
    result["region_rows"] = region_rows
    return result


@dataclass
class TactileFrameV2:
    frame_id: int
    height_rows: list[list[int]]
    region_rows: list[list[int]]
    regions: list[dict[str, Any]]
    scene_summary: str
    audio_overview: str
    self_review: dict[str, Any]
    checksum: str
    created_at: str
    version: int = FRAME_VERSION
    device_id: str = DEVICE_ID
    cols: int = COLS
    rows: int = ROWS
    height_encoding: str = HEIGHT_ENCODING

    @property
    def pins(self) -> list[int]:
        return [value for row in self.height_rows for value in row]

    @property
    def region_map(self) -> list[int]:
        return [value for row in self.region_rows for value in row]

    @property
    def levels(self) -> int:
        return 256

    @property
    def aspect_ratio(self) -> float:
        return self.cols / self.rows

    def region_at(self, row: int, col: int) -> dict[str, Any] | None:
        if row < 0 or row >= self.rows or col < 0 or col >= self.cols:
            return None
        region_id = self.region_rows[row][col]
        if region_id <= 0:
            return None
        return next((region for region in self.regions if region.get("id") == region_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "frame_id": self.frame_id,
            "device_id": self.device_id,
            "cols": self.cols,
            "rows": self.rows,
            "height_encoding": self.height_encoding,
            "levels": self.levels,
            "aspect_ratio": self.aspect_ratio,
            "pins": self.pins,
            "height_rows": self.height_rows,
            "region_rows": self.region_rows,
            "regions": self.regions,
            "scene_summary": self.scene_summary,
            "audio_overview": self.audio_overview,
            "self_review": self.self_review,
            "checksum": self.checksum,
            "created_at": self.created_at,
        }


def publish_tactile_frame(candidate: dict[str, Any], frame_id: int) -> TactileFrameV2:
    report = validate_tactile_candidate(candidate)
    if not report.valid:
        first = report.errors[0]
        raise ValueError(f"cannot publish invalid frame: {first.path}: {first.message}")

    canonical_candidate = materialize_tactile_candidate(candidate)
    core = {
        "version": FRAME_VERSION,
        "frame_id": int(frame_id),
        "device_id": DEVICE_ID,
        "height_rows": canonical_candidate["height_rows"],
        "region_rows": canonical_candidate["region_rows"],
        "regions": canonical_candidate["regions"],
        "scene_summary": canonical_candidate["scene_summary"],
        "audio_overview": canonical_candidate["audio_overview"],
    }
    canonical = json.dumps(core, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    checksum = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return TactileFrameV2(
        frame_id=int(frame_id),
        height_rows=[list(row) for row in canonical_candidate["height_rows"]],
        region_rows=[list(row) for row in canonical_candidate["region_rows"]],
        regions=[dict(region) for region in canonical_candidate["regions"]],
        scene_summary=str(canonical_candidate["scene_summary"]),
        audio_overview=str(canonical_candidate["audio_overview"]),
        self_review=dict(canonical_candidate.get("self_review") or {}),
        checksum=checksum,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
