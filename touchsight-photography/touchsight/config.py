"""Runtime configuration via environment variables (or .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@dataclass
class Settings:
    api_base: str = os.environ.get("TOUCHSIGHT_API_BASE", "https://api.openai.com/v1")
    api_key: str = os.environ.get("TOUCHSIGHT_API_KEY", "")
    model: str = os.environ.get("TOUCHSIGHT_MODEL", "gpt-4o")
    provider: str = os.environ.get("TOUCHSIGHT_PROVIDER", "openai")  # openai | mock
    input_dir: Path = PROJECT_ROOT / "input"
    runs_dir: Path = PROJECT_ROOT / "runs"
    max_agent_steps: int = int(os.environ.get("TOUCHSIGHT_MAX_STEPS", "12"))
    # 监听目录（分号分隔）：X5 文件传输模式挂载盘 + X5 网络摄像头模式的本地保存目录
    watch_dirs: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.watch_dirs is None:
            raw = os.environ.get(
                "TOUCHSIGHT_WATCH_DIRS",
                r"G:\DCIM\Camera01;C:\Users\L\Pictures\Camera Roll",
            )
            self.watch_dirs = [Path(p.strip()) for p in raw.split(";") if p.strip()]


settings = Settings()
