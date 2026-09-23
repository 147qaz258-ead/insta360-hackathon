"""语音链路（P8）：真实 API 的 ASR 与 TTS。

百炼工作空间网关的 compatible-mode 不支持音频通道，
但同源 DashScope 原生端点 /api/v1/services/aigc/multimodal-generation/generation 可用：
- ASR: qwen3-asr-flash，音频以 data:URI(base64) 传入，同步返回转写文本
- TTS: qwen3-tts-flash，返回 OSS 临时音频 URL，下载后得到 wav
"""
from __future__ import annotations

import base64

import requests

from touchsight.config import settings

ASR_MODEL = "qwen3-asr-flash-2026-02-10"
TTS_MODEL = "qwen3-tts-flash"
TTS_VOICE = "Cherry"
_TIMEOUT = 120


def _native_url() -> str:
    root = settings.api_base.replace("/compatible-mode/v1", "")
    return root + "/api/v1/services/aigc/multimodal-generation/generation"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"}


def asr_transcribe(audio_bytes: bytes, fmt: str = "wav") -> str:
    """音频字节 → 中文文本。fmt 为容器格式（wav/mp3/m4a/flac…）。"""
    data_uri = f"data:audio/{fmt};base64," + base64.b64encode(audio_bytes).decode()
    r = requests.post(
        _native_url(),
        headers=_headers(),
        json={
            "model": ASR_MODEL,
            "input": {"messages": [{"role": "user", "content": [{"audio": data_uri}]}]},
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    choices = r.json().get("output", {}).get("choices", [])
    if not choices:
        return ""
    parts = choices[0].get("message", {}).get("content", [])
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


def tts_speak(text: str) -> bytes:
    """中文文本 → wav 音频字节。"""
    r = requests.post(
        _native_url(),
        headers=_headers(),
        json={
            "model": TTS_MODEL,
            "input": {"text": text},
            "parameters": {"voice": TTS_VOICE},
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    url = r.json().get("output", {}).get("audio", {}).get("url")
    if not url:
        raise RuntimeError(f"TTS 未返回音频地址: {r.text[:200]}")
    audio = requests.get(url, timeout=_TIMEOUT)
    audio.raise_for_status()
    return audio.content
