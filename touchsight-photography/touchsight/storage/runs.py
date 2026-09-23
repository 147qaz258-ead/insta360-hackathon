"""Per-run artifact storage: run folder + trace.json (plan V5 §14)."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunSession:
    runs_dir: Path
    intent: str
    run_id: str = field(init=False)
    run_dir: Path = field(init=False)
    trace: list[dict] = field(default_factory=list)
    listeners: list = field(default_factory=list)

    def __post_init__(self):
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.runs_dir.glob("run_*"))
        next_n = 1
        if existing:
            try:
                next_n = int(existing[-1].name.split("_")[1]) + 1
            except (ValueError, IndexError):
                next_n = len(existing) + 1
        self.run_id = f"run_{next_n:03d}"
        self.run_dir = self.runs_dir / self.run_id
        (self.run_dir / "overview").mkdir(parents=True)
        (self.run_dir / "candidates").mkdir(parents=True)
        self.log("user_intent", {"intent": self.intent})

    def log(self, event_type: str, payload: Any):
        event = {
            "t": round(time.time(), 3),
            "run_id": self.run_id,
            "type": event_type,
            "data": payload,
        }
        self.trace.append(event)
        self.flush_trace()
        for fn in self.listeners:
            try:
                fn(event)
            except Exception:
                pass

    def flush_trace(self):
        (self.run_dir / "trace.json").write_text(
            json.dumps(self.trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_panorama(self, src: Path) -> Path:
        dst = self.run_dir / f"original_panorama{src.suffix.lower()}"
        shutil.copy2(src, dst)
        return dst

    def finalize(self, final_candidate: Path | None, summary: str):
        if final_candidate is not None:
            shutil.copy2(final_candidate, self.run_dir / f"final{final_candidate.suffix.lower()}")
        self.log("final_decision", {
            "final": str(final_candidate) if final_candidate else None,
            "summary": summary,
        })
