from __future__ import annotations

import os
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.env"


def load_local_environment(path: Path = DEFAULT_CONFIG_PATH) -> bool:
    """Load a small local KEY=VALUE file without overriding process settings."""
    if not path.is_file():
        return False

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return True
