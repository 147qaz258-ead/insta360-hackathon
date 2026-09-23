from __future__ import annotations

from typing import Any

from tactile.v4_frame import (
    COLS,
    DEVICE_ID,
    HEIGHT_ENCODING,
    ROWS,
    ValidationIssue,
    ValidationReport,
)


PROTOCOL_VERSION = "sparse-runs-v1"
PIN_COUNT = COLS * ROWS
MAX_REGION_ID = 255


def compute_content_rect(image_width: int, image_height: int) -> dict[str, int]:
    """Map the full source image onto the 80x48 grid with contain semantics.

    The image keeps its aspect ratio; leftover rows/columns stay lowered and
    are returned here so the model knows the exact drawable area in advance.
    """
    width = int(image_width)
    height = int(image_height)
    if width <= 0 or height <= 0:
        raise ValueError(f"image size must be positive, got {image_width}x{image_height}")
    scale = min(COLS / width, ROWS / height)
    rect_w = min(COLS, max(1, round(width * scale)))
    rect_h = min(ROWS, max(1, round(height * scale)))
    return {
        "x": (COLS - rect_w) // 2,
        "y": (ROWS - rect_h) // 2,
        "w": rect_w,
        "h": rect_h,
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


def _validate_regions(
    value: Any,
    errors: list[ValidationIssue],
) -> set[int]:
    region_ids: set[int] = set()
    if not isinstance(value, list) or not value:
        _issue(errors, "regions", "type", "regions 必须是非空数组", "non-empty array", type(value).__name__)
        return region_ids
    for index, region in enumerate(value):
        path = f"regions[{index}]"
        if not isinstance(region, dict):
            _issue(errors, path, "type", "区域必须是 object", "object", type(region).__name__)
            continue
        region_id = region.get("id")
        if isinstance(region_id, bool) or not isinstance(region_id, int) or not 1 <= region_id <= MAX_REGION_ID:
            _issue(errors, f"{path}.id", "region_id", f"区域 id 必须是 1..{MAX_REGION_ID} 的整数", f"1..{MAX_REGION_ID}", region_id)
        elif region_id in region_ids:
            _issue(errors, f"{path}.id", "duplicate", "区域 id 重复", "unique", region_id)
        else:
            region_ids.add(region_id)
        for field in ("name", "description", "speech"):
            if not isinstance(region.get(field), str) or not region.get(field, "").strip():
                _issue(errors, f"{path}.{field}", "required_text", f"{field} 必须是非空文本", "non-empty string", region.get(field))
    return region_ids


def validate_sparse_candidate(
    candidate: Any,
    content_rect: dict[str, int],
) -> ValidationReport:
    """Validate one sparse-runs-v1 candidate. Report exact errors; never repair."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    if not isinstance(candidate, dict):
        _issue(errors, "$", "type", "候选必须是 JSON object", "object", type(candidate).__name__)
        return ValidationReport(False, errors, warnings)

    expected_scalars = {
        "version": PROTOCOL_VERSION,
        "device_id": DEVICE_ID,
        "cols": COLS,
        "rows": ROWS,
        "height_encoding": HEIGHT_ENCODING,
    }
    for field, expected in expected_scalars.items():
        actual = candidate.get(field)
        if actual != expected:
            _issue(errors, field, "contract", f"{field} 与 sparse-runs-v1 协议不一致", expected, actual)

    submitted_rect = candidate.get("content_rect")
    if not isinstance(submitted_rect, dict):
        _issue(errors, "content_rect", "type", "content_rect 必须是 object", "object", type(submitted_rect).__name__)
    else:
        for field in ("x", "y", "w", "h"):
            actual = submitted_rect.get(field)
            if actual != content_rect[field]:
                _issue(
                    errors,
                    f"content_rect.{field}",
                    "content_rect",
                    "content_rect 必须由服务端 contain 规则预先给定且完全一致",
                    content_rect[field],
                    actual,
                )

    region_ids = _validate_regions(candidate.get("regions"), errors)

    for field in ("scene_summary", "audio_overview", "design_notes"):
        if not isinstance(candidate.get(field), str) or not candidate.get(field, "").strip():
            _issue(errors, field, "required_text", f"{field} 必须是非空文本", "non-empty string", candidate.get(field))

    runs = candidate.get("raised_runs")
    if not isinstance(runs, list):
        _issue(errors, "raised_runs", "type", "raised_runs 必须是数组", "array", type(runs).__name__)
        return ValidationReport(False, errors, warnings)

    normalized: list[dict[str, int]] = []
    for index, run in enumerate(runs):
        path = f"raised_runs[{index}]"
        if not isinstance(run, dict):
            _issue(errors, path, "type", "点段必须是 object", "object", type(run).__name__)
            continue
        row = run.get("row")
        start_col = run.get("start_col")
        end_col = run.get("end_col")
        height = run.get("height")
        region_id = run.get("region_id")
        run_ok = True
        for field, value, lo, hi in (
            ("row", row, 0, ROWS - 1),
            ("start_col", start_col, 0, COLS - 1),
            ("end_col", end_col, 0, COLS - 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                _issue(errors, f"{path}.{field}", "integer", f"{field} 必须是整数", f"{lo}..{hi}", value)
                run_ok = False
            elif value < lo or value > hi:
                _issue(errors, f"{path}.{field}", "out_of_bounds", f"{field} 越界", f"{lo}..{hi}", value)
                run_ok = False
        if run_ok and start_col > end_col:
            _issue(errors, f"{path}.end_col", "inverted_range", "end_col 必须大于等于 start_col", f">= {start_col}", end_col)
            run_ok = False
        if isinstance(height, bool) or not isinstance(height, int):
            _issue(errors, f"{path}.height", "integer", "height 必须是整数", "1..255", height)
            run_ok = False
        elif height < 1 or height > 255:
            _issue(errors, f"{path}.height", "illegal_height", "升起点段 height 必须在 1..255", "1..255", height)
            run_ok = False
        if isinstance(region_id, bool) or not isinstance(region_id, int):
            _issue(errors, f"{path}.region_id", "integer", "region_id 必须是整数", f"1..{MAX_REGION_ID}", region_id)
            run_ok = False
        elif region_id not in region_ids:
            _issue(errors, f"{path}.region_id", "unknown_region", "点段引用了 regions 中不存在的区域 id", sorted(region_ids), region_id)
            run_ok = False
        if run_ok:
            normalized.append({
                "row": row,
                "start_col": start_col,
                "end_col": end_col,
                "height": height,
                "region_id": region_id,
            })

    rect_x0 = content_rect["x"]
    rect_y0 = content_rect["y"]
    rect_x1 = rect_x0 + content_rect["w"] - 1
    rect_y1 = rect_y0 + content_rect["h"] - 1
    for index, run in enumerate(normalized):
        path = f"raised_runs[{index}]"
        if run["row"] < rect_y0 or run["row"] > rect_y1 or run["start_col"] < rect_x0 or run["end_col"] > rect_x1:
            _issue(
                errors,
                path,
                "outside_content_rect",
                "点段超出 content_rect；留边区域必须保持落下",
                f"row {rect_y0}..{rect_y1}, col {rect_x0}..{rect_x1}",
                f"row {run['row']}, col {run['start_col']}..{run['end_col']}",
            )

    runs_by_row: dict[int, list[dict[str, int]]] = {}
    for index, run in enumerate(normalized):
        runs_by_row.setdefault(run["row"], []).append({**run, "index": index})
    for row, row_runs in runs_by_row.items():
        row_runs.sort(key=lambda item: (item["start_col"], item["end_col"]))
        for previous, current in zip(row_runs, row_runs[1:]):
            if current["start_col"] <= previous["end_col"]:
                _issue(
                    errors,
                    f"raised_runs[{current['index']}]",
                    "overlap",
                    "同一行点段重叠；每个升起点只能由一个点段决定",
                    f"start_col > {previous['end_col']}",
                    f"row {row} col {current['start_col']} 与 raised_runs[{previous['index']}] col {previous['start_col']}..{previous['end_col']} 重叠",
                )

    return ValidationReport(not errors, errors, warnings)


def expand_sparse_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Losslessly expand validated runs into the full 48x80 matrices.

    Positions not covered by any run become height=0, region_id=0. The host
    never draws, connects, smooths or otherwise invents pin values.
    """
    height_rows = [[0] * COLS for _ in range(ROWS)]
    region_rows = [[0] * COLS for _ in range(ROWS)]
    for run in candidate.get("raised_runs") or []:
        row = run["row"]
        for col in range(run["start_col"], run["end_col"] + 1):
            height_rows[row][col] = run["height"]
            region_rows[row][col] = run["region_id"]
    return {"height_rows": height_rows, "region_rows": region_rows}


def compute_sparse_metrics(
    height_rows: list[list[int]],
    region_rows: list[list[int]],
    regions: list[dict[str, Any]],
    content_rect: dict[str, int],
) -> dict[str, Any]:
    """Mechanical facts for the Critic. No semantic judgement is made here."""
    pins = [value for row in height_rows for value in row]
    active = [value for value in pins if value > 0]
    active_count = len(active)

    longest_horizontal = 0
    for row in height_rows:
        streak = 0
        for value in row:
            streak = streak + 1 if value > 0 else 0
            longest_horizontal = max(longest_horizontal, streak)
    longest_vertical = 0
    for col in range(COLS):
        streak = 0
        for row in range(ROWS):
            streak = streak + 1 if height_rows[row][col] > 0 else 0
            longest_vertical = max(longest_vertical, streak)

    visited = [[False] * COLS for _ in range(ROWS)]
    largest_component = 0
    for row in range(ROWS):
        for col in range(COLS):
            if height_rows[row][col] <= 0 or visited[row][col]:
                continue
            stack = [(row, col)]
            visited[row][col] = True
            size = 0
            while stack:
                r, c = stack.pop()
                size += 1
                for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if 0 <= nr < ROWS and 0 <= nc < COLS and height_rows[nr][nc] > 0 and not visited[nr][nc]:
                        visited[nr][nc] = True
                        stack.append((nr, nc))
            largest_component = max(largest_component, size)

    solid_interior = 0
    for row in range(1, ROWS - 1):
        for col in range(1, COLS - 1):
            if height_rows[row][col] <= 0:
                continue
            if (
                height_rows[row - 1][col] > 0
                and height_rows[row + 1][col] > 0
                and height_rows[row][col - 1] > 0
                and height_rows[row][col + 1] > 0
            ):
                solid_interior += 1

    region_counts: dict[str, int] = {}
    for row in region_rows:
        for value in row:
            if value > 0:
                region_counts[str(value)] = region_counts.get(str(value), 0) + 1

    region_names = {str(region.get("id")): region.get("name") for region in regions}
    return {
        "pin_count": PIN_COUNT,
        "active_pins": active_count,
        "active_ratio": round(active_count / PIN_COUNT, 4),
        "target_active_ratio": "0.18-0.30 (软目标)",
        "height_stats": {
            "min": min(active) if active else 0,
            "max": max(active) if active else 0,
            "mean": round(sum(active) / active_count, 1) if active else 0,
            "distinct_levels": len(set(active)),
        },
        "region_pin_counts": {
            f"{region_id}:{region_names.get(region_id) or '?'}": count
            for region_id, count in sorted(region_counts.items(), key=lambda item: int(item[0]))
        },
        "longest_horizontal_run": longest_horizontal,
        "longest_vertical_run": longest_vertical,
        "largest_component_pins": largest_component,
        "largest_component_ratio_of_active": round(largest_component / active_count, 4) if active_count else 0,
        "solid_interior_ratio_of_active": round(solid_interior / active_count, 4) if active_count else 0,
        "content_rect": content_rect,
    }
