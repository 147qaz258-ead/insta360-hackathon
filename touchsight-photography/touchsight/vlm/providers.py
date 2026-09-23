"""Vision model provider abstraction.

Only requirements on the model (per plan V5 §11):
- multi-image understanding
- natural-language visual reasoning
- structured output
- tool / function calling

The default implementation talks to any OpenAI-compatible chat-completions
endpoint (OpenAI, DashScope compatible-mode, GLM, Gemini OpenAI-compat, ...),
so the competition model can be swapped via env vars without code changes.
"""
from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import requests
from PIL import Image

MAX_IMAGE_DIM = 1568  # 限制上传图像长边，控制请求体积（qwen-vl 推荐尺寸附近）


def image_content(path: str | Path, detail: str = "high") -> dict:
    img = Image.open(path)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_IMAGE_DIM:
        scale = MAX_IMAGE_DIM / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{data}", "detail": detail},
    }


def text_content(text: str) -> dict:
    return {"type": "text", "text": text}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class VisionModelProvider(Protocol):
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
    ) -> ChatResult: ...


class OpenAICompatibleProvider:
    def __init__(self, api_base: str, api_key: str, model: str, timeout: int = 180):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=self.timeout,
                )
                break
            except requests.RequestException as e:
                last_err = e
                print(f"[vlm] 请求异常(第{attempt+1}次): {e}")
        else:
            raise RuntimeError(f"VLM 请求两次均失败: {last_err}")
        if resp.status_code != 200:
            raise RuntimeError(f"VLM API error {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        choice = data["choices"][0]["message"]
        result = ChatResult(raw=data)
        content = choice.get("content")
        if isinstance(content, str):
            result.text = content
        elif isinstance(content, list):
            result.text = "".join(
                part.get("text", "") for part in content if part.get("type") == "text"
            )
        for tc in choice.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result.tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
        return result


class MockProvider:
    """Offline provider driven by a scripted queue, for pipeline testing without API."""

    def __init__(self, script: list[ChatResult] | None = None):
        self.script = list(script or [])
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, json_mode=False) -> ChatResult:
        self.calls.append({"messages": messages, "tools": tools})
        if self.script:
            return self.script.pop(0)
        return ChatResult(text="[mock] 无更多脚本响应")


def make_provider(settings) -> VisionModelProvider:
    if settings.provider == "mock":
        return MockProvider()
    if not settings.api_key:
        raise RuntimeError(
            "未配置 TOUCHSIGHT_API_KEY。请在 .env 中设置 TOUCHSIGHT_API_BASE / "
            "TOUCHSIGHT_API_KEY / TOUCHSIGHT_MODEL，或用 --provider mock 离线测试。"
        )
    return OpenAICompatibleProvider(
        settings.api_base, settings.api_key, settings.model,
        timeout=int(os.environ.get("TOUCHSIGHT_VLM_TIMEOUT", "480")),
    )
