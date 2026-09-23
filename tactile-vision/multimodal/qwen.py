from __future__ import annotations

import base64
import binascii
import http.client
import hashlib
import io
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from PIL import Image

from .base import MultimodalProvider, MultimodalProviderError
from .prompts import (
    SCENE_UNDERSTANDING_SYSTEM,
    SCENE_UNDERSTANDING_USER,
    TACTILE_AGENT_V5_BLIND_READ_SYSTEM,
    TACTILE_AGENT_V5_BLIND_READ_USER,
    TACTILE_AGENT_V5_CRITIC_SYSTEM,
    TACTILE_AGENT_V5_CRITIC_USER,
    TACTILE_AGENT_V5_DESIGN_SYSTEM,
    TACTILE_AGENT_V5_DESIGN_USER,
    TACTILE_AGENT_V5_UNDERSTAND_SYSTEM,
    TACTILE_AGENT_V5_UNDERSTAND_USER,
    TACTILE_CRITIC_SYSTEM,
    TACTILE_CRITIC_USER,
    TACTILE_PLANNER_SYSTEM,
    TOUCH_EXPLAIN_SYSTEM,
    TOUCH_EXPLAIN_USER,
    TACTILE_AGENT_V4_REVIEW_SYSTEM,
    TACTILE_AGENT_V4_MANIFEST_SYSTEM,
    TACTILE_AGENT_V4_ROWS_SYSTEM,
    TACTILE_AGENT_V4_SYSTEM,
    TACTILE_AGENT_V4_USER,
)


_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_TRANSIENT_NET_ERRORS = (
    ssl.SSLError,
    ConnectionResetError,
    http.client.RemoteDisconnected,
    socket.gaierror,
)


class _SmallSegmentHTTPSConnection(http.client.HTTPSConnection):
    """The route to DashScope's OSS frontend black-holes larger TCP
    bursts (bodies past ~4-8 KB stall mid-upload); capping the send
    buffer keeps emitted segments below that threshold."""

    def connect(self) -> None:
        super().connect()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _JSON_FENCE_RE.sub("", text.strip()).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for start, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned, start)
            if isinstance(value, dict):
                candidates.append((end - start, value))
        except json.JSONDecodeError:
            continue
    if candidates:
        # Some compatible gateways append a second metadata JSON object. The
        # actual model payload is the largest complete object; trailing data
        # must not make an otherwise valid candidate unusable.
        return max(candidates, key=lambda item: item[0])[1]
    preview = cleaned[:240].replace("\r", " ").replace("\n", " ")
    suffix = cleaned[-120:].replace("\r", " ").replace("\n", " ")
    raise MultimodalProviderError(
        f"VLM response did not contain a complete JSON object "
        f"(chars={len(cleaned)}, prefix={preview!r}, suffix={suffix!r})"
    )


class Qwen3VLApiProvider(MultimodalProvider):
    def __init__(
        self,
        credential: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        enable_thinking: bool | None = None,
        api_mode: str | None = None,
        stream: bool | None = None,
    ) -> None:
        self.credential = credential or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = (
            base_url
            or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.model = model or os.getenv("QWEN_VL_MODEL", "qwen3.8-max")
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None else os.getenv("QWEN_API_TIMEOUT", "90"))
        if enable_thinking is None:
            configured = os.getenv("QWEN_ENABLE_THINKING", "false").strip().casefold()
            enable_thinking = configured in {"1", "true", "yes", "on"}
        self.enable_thinking = bool(enable_thinking)
        if stream is None:
            configured_stream = os.getenv("QWEN_STREAM", "false").strip().casefold()
            stream = configured_stream in {"1", "true", "yes", "on"}
        self.stream = bool(stream)
        configured_fallback = os.getenv("QWEN_ALLOW_COMPATIBLE_FALLBACK", "true").strip().casefold()
        self.allow_compatible_fallback = configured_fallback in {"1", "true", "yes", "on"}
        self.api_mode = (api_mode or os.getenv("QWEN_API_MODE", "dashscope-native")).strip().casefold()
        native_base = os.getenv("DASHSCOPE_NATIVE_BASE_URL", "").strip()
        if not native_base:
            native_base = re.sub(r"/compatible-mode/v1/?$", "/api/v1", self.base_url)
        self.native_base_url = native_base.rstrip("/")
        self._upload_policy: dict[str, Any] | None = None
        self._image_oss_cache: dict[str, str] = {}
        self._model_image_cache: dict[str, str] = {}

    def _model_image_data_url(self, image_data_url: str) -> str:
        """Create a bounded vision copy while preserving the original asset.

        The tactile surface is only 80x48. Sending a multi-megabyte PNG on
        every multimodal turn adds vision tokens and gateway latency without
        increasing the final spatial precision. The browser/runtime still
        retain the untouched original; only model calls use this cached JPEG.
        """
        self._validate_image_data_url(image_data_url)
        digest = hashlib.sha256(image_data_url.encode("utf-8")).hexdigest()
        cached = self._model_image_cache.get(digest)
        if cached:
            return cached
        header, _, encoded = image_data_url.partition(",")
        try:
            raw = base64.b64decode(encoded)
            with Image.open(io.BytesIO(raw)) as source:
                image = source.convert("RGB")
                if max(image.size) <= 1024 and len(raw) <= 700_000:
                    self._model_image_cache[digest] = image_data_url
                    return image_data_url
                image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=88, optimize=True)
        except (OSError, ValueError, binascii.Error) as exc:
            raise MultimodalProviderError("image could not be prepared for the vision model") from exc
        optimized = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        self._model_image_cache[digest] = optimized
        return optimized

    def _open_with_retry(self, request: urllib.request.Request, timeout: float, attempts: int = 3):
        """The workspace gateway occasionally drops TLS mid-handshake; a
        fresh connection usually succeeds immediately, so fail fast on
        transient resets and retry. Full timeouts are not retried to avoid
        multiplying long waits."""
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return urllib.request.urlopen(request, timeout=timeout)
            except (ssl.SSLError, ConnectionResetError, http.client.RemoteDisconnected, socket.gaierror) as exc:
                last = exc
            except urllib.error.URLError as exc:
                if not isinstance(exc.reason, _TRANSIENT_NET_ERRORS):
                    raise
                last = exc
            if attempt + 1 < attempts:
                time.sleep(1.2 * (attempt + 1))
        raise last if last else RuntimeError("unreachable retry state")

    def _oss_post_form(self, upload_host: str, body: bytes, content_type: str) -> None:
        parsed = urllib.parse.urlsplit(upload_host)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        timeout = min(self.timeout_seconds, 90)
        last: Exception | None = None
        for attempt in range(3):
            conn: http.client.HTTPSConnection | None = None
            try:
                conn = _SmallSegmentHTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
                conn.request("POST", path, body=body, headers={"Content-Type": content_type})
                response = conn.getresponse()
                detail = response.read().decode("utf-8", errors="replace")[:500]
                if response.status not in (200, 201):
                    raise MultimodalProviderError(f"image upload failed with HTTP {response.status}: {detail}")
                return
            except MultimodalProviderError:
                raise
            except (OSError, http.client.HTTPException) as exc:
                last = exc
                if attempt + 1 < 3:
                    time.sleep(1.2 * (attempt + 1))
            finally:
                if conn is not None:
                    conn.close()
        raise MultimodalProviderError(f"image upload connection failed: {last}")

    def status(self) -> dict[str, Any]:
        return {
            "provider": "dashscope-multimodal" if self.api_mode == "dashscope-native" else "openai-compatible-multimodal",
            "configured": bool(self.credential),
            "model": self.model,
            "base_url": self.native_base_url if self.api_mode == "dashscope-native" else self.base_url,
            "api_mode": self.api_mode,
            "timeout_seconds": self.timeout_seconds,
            "thinking": self.enable_thinking,
            "stream": self.stream,
        }

    @staticmethod
    def _validate_image_data_url(image_data_url: str) -> None:
        if not isinstance(image_data_url, str) or not image_data_url.startswith("data:image/"):
            raise MultimodalProviderError("image must be a base64 data URL")
        if ";base64," not in image_data_url[:128]:
            raise MultimodalProviderError("image data URL must use base64 encoding")

    def _request_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.credential:
            raise MultimodalProviderError("Qwen API credential is not configured in the environment")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.credential,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-DashScope-OssResourceResolve": "enable",
        }
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers=headers,
        )

        try:
            with self._open_with_retry(request, self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise MultimodalProviderError(f"Qwen API HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MultimodalProviderError(f"Qwen API network error: {exc.reason}") from exc
        except (TimeoutError, http.client.RemoteDisconnected, ConnectionResetError) as exc:
            raise MultimodalProviderError("Qwen API request timed out") from exc

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MultimodalProviderError("Qwen API returned invalid JSON") from exc

    def _post_sse_text(
        self,
        url: str,
        payload: dict[str, Any],
        extra_headers: dict[str, str],
        extract: Any,
    ) -> str:
        """POST an SSE request and collect text under an absolute watchdog.

        The gateway sometimes holds a stream open with keepalive bytes
        while never producing content (or dribbles headers), which defeats
        per-recv socket timeouts. A watchdog thread closing the connection
        is the only reliable cap for that failure mode.
        """
        if not self.credential:
            raise MultimodalProviderError("Qwen API credential is not configured in the environment")
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.credential,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **extra_headers,
        }
        last: Exception | None = None
        for attempt in range(2):
            conn = _SmallSegmentHTTPSConnection(parsed.hostname, parsed.port or 443, timeout=self.timeout_seconds)
            expired = threading.Event()
            def _abort() -> None:
                expired.set()
                conn.close()
            watchdog = threading.Timer(self.timeout_seconds, _abort)
            watchdog.daemon = True
            parts: list[str] = []
            completed = False
            try:
                watchdog.start()
                conn.request("POST", path, body=body, headers=headers)
                response = conn.getresponse()
                if response.status != 200:
                    detail = response.read().decode("utf-8", errors="replace")[:2000]
                    raise MultimodalProviderError(f"Qwen API HTTP {response.status}: {detail}")
                while True:
                    raw_line = response.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if not value or value == "[DONE]":
                        continue
                    try:
                        event = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                    parts.extend(extract(event))
                completed = True
            except MultimodalProviderError:
                raise
            except (OSError, http.client.HTTPException) as exc:
                if expired.is_set():
                    raise MultimodalProviderError("Qwen API request timed out") from exc
                last = exc
                if attempt + 1 < 2:
                    time.sleep(1.2)
            finally:
                watchdog.cancel()
                conn.close()
            if completed:
                if parts:
                    return "".join(parts)
                # A clean 200 with no usable text is a stall variant; retry once.
                last = MultimodalProviderError("Qwen API stream did not contain text output")
        if isinstance(last, MultimodalProviderError):
            raise last
        raise MultimodalProviderError(f"Qwen API network error: {last}")

    def _request_dashscope_stream(self, url: str, payload: dict[str, Any]) -> str:
        def extract(event: dict[str, Any]) -> list[str]:
            try:
                content = event["output"]["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                return []
            if isinstance(content, str):
                return [content]
            if isinstance(content, list):
                return [str(item.get("text", "")) for item in content if isinstance(item, dict) and "text" in item]
            return []

        return self._post_sse_text(
            url,
            payload,
            {
                "X-DashScope-SSE": "enable",
                "X-DashScope-OssResourceResolve": "enable",
            },
            extract,
        )

    @staticmethod
    def _to_dashscope_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, list):
                media_parts: list[dict[str, Any]] = []
                text_parts: list[dict[str, Any]] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        text_parts.append({"text": str(item.get("text", ""))})
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url", {})
                        if isinstance(image_url, dict):
                            image_url = image_url.get("url", "")
                        media_parts.append({"image": str(image_url)})
                # DashScope's native multimodal endpoint expects media before
                # the accompanying text. Text-first Base64 requests can stall.
                content = media_parts + text_parts
            converted.append({"role": str(message.get("role", "user")), "content": content})
        return converted

    def _get_upload_policy(self) -> dict[str, Any]:
        now = time.time()
        if self._upload_policy and now < self._upload_policy.get("_expires_at", 0):
            return self._upload_policy
        if not self.credential:
            raise MultimodalProviderError("Qwen API credential is not configured in the environment")
        url = self.native_base_url + "/uploads?action=getPolicy&model=" + urllib.parse.quote(self.model)
        request = urllib.request.Request(url, method="GET", headers={"Authorization": "Bearer " + self.credential})
        try:
            with self._open_with_retry(request, min(self.timeout_seconds, 30), attempts=5) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise MultimodalProviderError(f"upload policy request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MultimodalProviderError(f"upload policy network error: {exc.reason}") from exc
        except (TimeoutError, http.client.RemoteDisconnected, ConnectionResetError, ssl.SSLError) as exc:
            raise MultimodalProviderError(f"upload policy connection failed: {exc}") from exc

        payload = data.get("data") if isinstance(data, dict) else None
        required = ("upload_host", "upload_dir", "oss_access_key_id", "policy", "signature")
        if not isinstance(payload, dict) or any(not payload.get(field) for field in required):
            raise MultimodalProviderError("DashScope upload policy response is incomplete")
        expire_in = payload.get("expire_in_seconds")
        try:
            ttl = float(expire_in) - 60.0 if expire_in else 300.0
        except (TypeError, ValueError):
            ttl = 300.0
        payload["_expires_at"] = now + max(60.0, ttl)
        self._upload_policy = payload
        return payload

    def _upload_data_url_to_oss(self, image_data_url: str, refreshed: bool = False) -> str:
        """The workspace gateway stalls indefinitely on inline base64 data
        URLs. Uploading to DashScope's temporary OSS and referencing the
        object via oss:// + X-DashScope-OssResourceResolve is the path that
        returns within seconds."""
        header, _, encoded = image_data_url.partition(",")
        mime = header[5:].split(";", 1)[0] or "image/jpeg"
        try:
            raw = base64.b64decode(encoded)
        except (binascii.Error, ValueError) as exc:
            raise MultimodalProviderError("image data URL base64 is malformed") from exc
        if not raw:
            raise MultimodalProviderError("image data URL contains an empty payload")

        digest = hashlib.sha256(raw).hexdigest()
        cached = self._image_oss_cache.get(digest)
        if cached:
            return cached

        policy = self._get_upload_policy()
        max_mb = policy.get("max_file_size_mb")
        try:
            if max_mb and len(raw) > float(max_mb) * 1024 * 1024:
                raise MultimodalProviderError(f"image exceeds upload limit of {max_mb} MB")
        except (TypeError, ValueError):
            pass

        extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
        filename = "img-" + uuid.uuid4().hex[:12] + extension
        object_key = policy["upload_dir"].rstrip("/") + "/" + filename

        boundary = "----DashScopeUpload" + uuid.uuid4().hex
        form_fields = (
            ("key", object_key),
            ("x-oss-forbid-overwrite", str(policy.get("x_oss_forbid_overwrite", "true")).lower()),
            ("x-oss-object-acl", str(policy.get("x_oss_object_acl", "private"))),
            ("OSSAccessKeyId", policy["oss_access_key_id"]),
            ("policy", policy["policy"]),
            ("signature", policy["signature"]),
            ("success_action_status", "200"),
        )
        body = b""
        for name, value in form_fields:
            body += (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
            ).encode("utf-8")
        body += (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + raw + b"\r\n" + f"--{boundary}--\r\n".encode("utf-8")

        try:
            self._oss_post_form(
                policy["upload_host"],
                body,
                "multipart/form-data; boundary=" + boundary,
            )
        except MultimodalProviderError as exc:
            # The upload policy lives for a very short time; a flaky gateway
            # can push the first POST past its expiry. Refresh once and retry.
            if refreshed or ("403" not in str(exc) and "Policy expired" not in str(exc)):
                raise
            self._upload_policy = None
            return self._upload_data_url_to_oss(image_data_url, refreshed=True)
        resolved = "oss://" + object_key
        self._image_oss_cache[digest] = resolved
        return resolved

    def _resolve_native_images(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                resolved.append(message)
                continue
            new_parts: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict) and item.get("image", "").startswith("data:image/"):
                    new_parts.append({"image": self._upload_data_url_to_oss(str(item["image"]))})
                else:
                    new_parts.append(item)
            resolved.append({**message, "content": new_parts})
        return resolved

    @staticmethod
    def _is_transient_failure(message: str) -> bool:
        lowered = message.casefold()
        return any(marker in lowered for marker in (
            "network error",
            "timed out",
            "connection failed",
            "connection reset",
            "remote end closed",
        ))

    def _compatible_chat_content(self, messages: list[dict[str, Any]], max_tokens: int) -> Any:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "enable_thinking": self.enable_thinking,
        }
        if self.stream:
            payload["stream"] = True
            return self._request_openai_stream(self.base_url + "/chat/completions", payload)
        data = self._request_json(self.base_url + "/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MultimodalProviderError("Qwen API returned an unexpected OpenAI response shape") from exc

    def _request_openai_stream(self, url: str, payload: dict[str, Any]) -> str:
        """Read OpenAI-compatible SSE deltas under the same watchdog."""
        def extract(event: dict[str, Any]) -> list[str]:
            try:
                choice = event["choices"][0]
                delta = choice.get("delta") or choice.get("message") or {}
                content = delta.get("content", "")
            except (KeyError, IndexError, TypeError, AttributeError):
                return []
            if isinstance(content, str):
                return [content] if content else []
            if isinstance(content, list):
                return [item["text"] for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
            return []

        return self._post_sse_text(url, payload, {}, extract)

    def _chat(self, messages: list[dict[str, Any]], max_tokens: int = 3500) -> dict[str, Any]:
        used_provider = self.status()["provider"]
        if self.api_mode == "dashscope-native":
            try:
                dashscope_messages = self._resolve_native_images(self._to_dashscope_messages(messages))
                payload = {
                    "model": self.model,
                    "input": {"messages": dashscope_messages},
                    "parameters": {
                        "result_format": "message",
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                        # Qwen 3.8 Max defaults to xhigh reasoning. Structured
                        # extraction is faster and more repeatable without it.
                        "enable_thinking": self.enable_thinking,
                    },
                }
                endpoint = self.native_base_url + "/services/aigc/multimodal-generation/generation"
                if self.stream:
                    payload["parameters"]["incremental_output"] = True
                    content = self._request_dashscope_stream(endpoint, payload)
                else:
                    data = self._request_json(endpoint, payload)
                    try:
                        choice = data["output"]["choices"][0]
                        message = choice["message"]
                        content = message.get("content")
                        if not content:
                            content = message.get("reasoning_content") or message.get("reasoning") or ""
                    except (KeyError, IndexError, TypeError) as exc:
                        raise MultimodalProviderError("Qwen API returned an unexpected DashScope response shape") from exc
            except MultimodalProviderError as exc:
                if not self._is_transient_failure(str(exc)) or not self.allow_compatible_fallback:
                    raise
                # The workspace exposes both native and OpenAI-compatible
                # endpoints for the same model. A native OSS/gateway outage
                # must not destroy an otherwise valid run, so retry the exact
                # same multimodal request through the compatible transport.
                content = self._compatible_chat_content(messages, max_tokens)
                used_provider = "openai-compatible-multimodal-fallback"
        else:
            content = self._compatible_chat_content(messages, max_tokens)

        if isinstance(content, list):
            # Native DashScope parts are {"text": ...}; OpenAI-style parts are
            # {"type": "text", "text": ...}. Accept both or every native
            # multimodal response would be filtered down to an empty string.
            parts = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item.get("text", "")))
            content = "\n".join(parts)
        if not isinstance(content, str):
            raise MultimodalProviderError("Qwen API response content is not text")
        if not content.strip():
            finish_reason = "unknown"
            message_keys: list[str] = []
            try:
                finish_reason = str(choice.get("finish_reason", "unknown"))
                message_keys = sorted(str(key) for key in message.keys())
            except (NameError, AttributeError):
                pass
            raise MultimodalProviderError(
                f"Qwen API returned empty content (finish_reason={finish_reason}, message_keys={message_keys})"
            )

        result = _extract_json_object(content)
        meta = result.setdefault("_meta", {})
        if isinstance(meta, dict):
            meta.update({"provider": used_provider, "model": self.model})
        return result

    def understand_image(self, image_data_url: str) -> dict[str, Any]:
        self._validate_image_data_url(image_data_url)
        model_image_data_url = self._model_image_data_url(image_data_url)
        return self._chat([
            {"role": "system", "content": SCENE_UNDERSTANDING_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": SCENE_UNDERSTANDING_USER},
                    {"type": "image_url", "image_url": {"url": model_image_data_url}},
                ],
            },
        ], max_tokens=6000)

    def _structured_chat_retry(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        attempts: int = 3,
    ) -> dict[str, Any]:
        last: MultimodalProviderError | None = None
        for attempt in range(max(1, attempts)):
            try:
                return self._chat(messages, max_tokens=max_tokens)
            except MultimodalProviderError as exc:
                last = exc
                message = str(exc)
                transient_network = self._is_transient_failure(message)
                if not transient_network and "empty content" not in message and "complete JSON object" not in message:
                    raise
                if attempt + 1 < max(1, attempts):
                    # The workspace gateway drops TLS in bursts; a short cool
                    # down between call-level retries recovers without
                    # failing the whole agent run.
                    time.sleep(4.0 * (attempt + 1))
        raise last or MultimodalProviderError("structured model call failed")

    def plan_tactile(
        self,
        image_data_url: str,
        scene_understanding: dict[str, Any],
        grounded_scene: dict[str, Any] | None = None,
        device_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_image_data_url(image_data_url)
        context = {
            "scene_understanding": scene_understanding,
            "grounded_scene": grounded_scene or {},
            "device_profile": device_profile or {},
        }
        return self._chat([
            {"role": "system", "content": TACTILE_PLANNER_SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "根据场景事实设计 TactilePlan，不得直接生成 pins。输入 JSON：\n"
                        + json.dumps(context, ensure_ascii=False),
                    },
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ])

    def critic_review(
        self,
        image_data_url: str,
        preview_data_url: str,
        tactile_plan: dict[str, Any],
        metrics: dict[str, Any],
        device_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_image_data_url(image_data_url)
        self._validate_image_data_url(preview_data_url)
        context = {
            "tactile_plan": tactile_plan,
            "metrics": metrics,
            "device_profile": device_profile or {},
        }
        return self._chat([
            {"role": "system", "content": TACTILE_CRITIC_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "image_url", "image_url": {"url": preview_data_url}},
                    {
                        "type": "text",
                        "text": TACTILE_CRITIC_USER.replace(
                            "{context}", json.dumps(context, ensure_ascii=False)
                        ),
                    },
                ],
            },
        ])

    def explain_touch(
        self,
        image_data_url: str,
        scene_summary: str,
        region_info: dict[str, Any],
        question: str,
    ) -> dict[str, Any]:
        self._validate_image_data_url(image_data_url)
        user_text = (
            TOUCH_EXPLAIN_USER.replace("{scene_summary}", scene_summary or "（无）")
            .replace("{region_info}", json.dumps(region_info, ensure_ascii=False))
            .replace("{question}", question or "用户刚触摸了这个区域，它是什么？")
        )
        return self._chat([
            {"role": "system", "content": TOUCH_EXPLAIN_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": user_text},
                ],
            },
        ], max_tokens=800)

    def generate_tactile_candidate(
        self,
        image_data_url: str,
        *,
        candidate_number: int,
        user_instruction: str = "",
        feedback: list[dict[str, Any]] | None = None,
        previous_candidate: dict[str, Any] | None = None,
        preview_data_url: str | None = None,
    ) -> dict[str, Any]:
        """Build one authoritative candidate through bounded tool-sized calls.

        The model first writes its own manifest, then directly writes every
        hardware row in bounded six-row chunks. The host only concatenates those rows;
        it never rasterizes the manifest or invents pin values.
        """
        self._validate_image_data_url(image_data_url)
        model_image_data_url = self._model_image_data_url(image_data_url)
        feedback_text = "当前没有协议错误或修订意见。"
        if feedback:
            feedback_text = "工具返回的精确问题如下，请在新候选中自行修复：\n" + json.dumps(
                feedback, ensure_ascii=False
            )
        manifest_context: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": model_image_data_url}},
        ]
        if preview_data_url:
            self._validate_image_data_url(preview_data_url)
            manifest_context.append({"type": "image_url", "image_url": {"url": preview_data_url}})
        manifest_request = (
            f"这是第 {candidate_number} 个完整候选。用户要求："
            f"{user_instruction.strip() or '无额外要求，按图片自主设计。'}\n{feedback_text}"
        )
        if previous_candidate:
            previous_manifest = {
                key: previous_candidate.get(key)
                for key in ("regions", "scene_summary", "audio_overview", "design_spec", "self_review")
            }
            manifest_request += "\n上一候选清单：\n" + json.dumps(previous_manifest, ensure_ascii=False)
        manifest_context.append({"type": "text", "text": manifest_request})
        manifest: dict[str, Any] = {}
        manifest_errors: list[str] = []
        for manifest_attempt in range(3):
            attempt_context = list(manifest_context)
            if manifest_errors:
                attempt_context.append({
                    "type": "text",
                    "text": (
                        "上一份清单未通过机械协议检查。请缩短文字但返回完整顶层 JSON，"
                        "不要只返回 design_spec 内层对象。精确错误：\n"
                        + json.dumps(manifest_errors, ensure_ascii=False)
                    ),
                })
            manifest = self._structured_chat_retry([
                {"role": "system", "content": TACTILE_AGENT_V4_MANIFEST_SYSTEM},
                {"role": "user", "content": attempt_context},
            ], max_tokens=2200, attempts=2)
            manifest_errors = self._manifest_errors(manifest)
            if not manifest_errors:
                break
        if manifest_errors:
            raise MultimodalProviderError(
                "manifest protocol check failed: " + "; ".join(manifest_errors[:12])
            )

        height_rows_rle: list[Any] = []
        region_rows_rle: list[Any] = []
        completed_rows: list[dict[str, Any]] = []
        for row_start in range(0, 48, 6):
            row_end = min(47, row_start + 5)
            chunk_feedback: list[str] = []
            chunk: dict[str, Any] = {}
            for chunk_attempt in range(3):
                request = {
                    "candidate_number": candidate_number,
                    "row_start": row_start,
                    "row_end": row_end,
                    "manifest": manifest,
                    # Feed the model its own immediately preceding hardware rows.
                    # This is agent memory, not host-side rasterization: the host
                    # never changes a height or region value.
                    "previous_rows_tail": completed_rows[-4:],
                    "continuity_requirement": (
                        "延续 previous_rows_tail 中的真实轮廓端点和区域关系，"
                        "但禁止复制成粗横带或把 envelope/bbox 填成实心矩形。"
                    ),
                    "tool_feedback": chunk_feedback,
                }
                chunk = self._structured_chat_retry([
                    {"role": "system", "content": TACTILE_AGENT_V4_ROWS_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": model_image_data_url}},
                            {"type": "text", "text": json.dumps(request, ensure_ascii=False)},
                        ],
                    },
                ], max_tokens=1200, attempts=2)
                chunk_feedback = self._row_chunk_errors(chunk, row_start, row_end, manifest)
                if not chunk_feedback:
                    break
            chunk_rows = chunk.get("rows") if isinstance(chunk.get("rows"), list) else []
            for row_item in chunk_rows:
                if not isinstance(row_item, dict):
                    continue
                height_rows_rle.append(row_item.get("height_rle") or [])
                region_rows_rle.append(row_item.get("region_rle") or [])
                completed_rows.append(row_item)

        return {
            **manifest,
            "height_rows_rle": height_rows_rle,
            "region_rows_rle": region_rows_rle,
        }

    @staticmethod
    def _decode_rle_row(runs: Any) -> list[int] | None:
        if not isinstance(runs, list):
            return None
        row: list[int] = []
        for run in runs:
            if not isinstance(run, list) or len(run) != 2:
                return None
            count, value = run
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            row.extend([value] * count)
        return row if len(row) == 80 else None

    @staticmethod
    def _manifest_errors(manifest: Any) -> list[str]:
        """Validate only the manifest transport contract; never alter design."""
        if not isinstance(manifest, dict):
            return ["manifest 必须是 JSON object"]
        errors: list[str] = []
        required_values = {
            "version": 2,
            "device_id": "rdk_hdmi",
            "cols": 80,
            "rows": 48,
            "height_encoding": "uint8",
        }
        for field, expected in required_values.items():
            if manifest.get(field) != expected:
                errors.append(f"{field} 必须是 {expected!r}，实际为 {manifest.get(field)!r}")
        regions = manifest.get("regions")
        if not isinstance(regions, list) or not regions:
            errors.append("regions 必须是非空数组")
        else:
            seen: set[int] = set()
            for index, region in enumerate(regions):
                if not isinstance(region, dict):
                    errors.append(f"regions[{index}] 必须是 object")
                    continue
                region_id = region.get("id")
                if isinstance(region_id, bool) or not isinstance(region_id, int) or not 1 <= region_id <= 255:
                    errors.append(f"regions[{index}].id 必须是 1..255 整数")
                elif region_id in seen:
                    errors.append(f"regions[{index}].id={region_id} 重复")
                else:
                    seen.add(region_id)
                for field in ("name", "description", "speech"):
                    if not isinstance(region.get(field), str) or not region[field].strip():
                        errors.append(f"regions[{index}].{field} 必须是非空文本")
        for field in ("scene_summary", "audio_overview"):
            if not isinstance(manifest.get(field), str) or not manifest[field].strip():
                errors.append(f"{field} 必须是非空文本")
        if not isinstance(manifest.get("design_spec"), dict):
            errors.append("design_spec 必须是 object")
        if not isinstance(manifest.get("self_review"), dict):
            errors.append("self_review 必须是 object")
        return errors

    @classmethod
    def _row_chunk_errors(
        cls,
        chunk: dict[str, Any],
        row_start: int,
        row_end: int,
        manifest: dict[str, Any] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        expected_rows = row_end - row_start + 1
        if chunk.get("row_start") != row_start:
            errors.append(f"row_start 必须是 {row_start}，实际为 {chunk.get('row_start')}")
        if chunk.get("row_end") != row_end:
            errors.append(f"row_end 必须是 {row_end}，实际为 {chunk.get('row_end')}")
        rows = chunk.get("rows")
        if not isinstance(rows, list) or len(rows) != expected_rows:
            errors.append(f"rows 必须包含 {expected_rows} 个行对象")
            return errors
        known_regions = {
            region.get("id")
            for region in (manifest or {}).get("regions") or []
            if isinstance(region, dict) and isinstance(region.get("id"), int)
        }
        for offset, row_item in enumerate(rows):
            expected_row = row_start + offset
            if not isinstance(row_item, dict):
                errors.append(f"rows[{offset}] 必须是 object")
                continue
            if row_item.get("row") != expected_row:
                errors.append(f"rows[{offset}].row 必须是 {expected_row}")
            for field, maximum in (("height_rle", 255), ("region_rle", None)):
                runs = row_item.get(field)
                if not isinstance(runs, list):
                    errors.append(f"rows[{offset}].{field} 必须是 RLE 数组")
                    continue
                total = 0
                for run in runs:
                    if not isinstance(run, list) or len(run) != 2:
                        errors.append(f"rows[{offset}].{field} 包含无效 RLE 段")
                        continue
                    count, value = run
                    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                        errors.append(f"rows[{offset}].{field} 的 count 必须为正整数")
                    else:
                        total += count
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or (maximum is not None and value > maximum):
                        errors.append(f"rows[{offset}].{field} 的 value 超出范围")
                if total != 80:
                    errors.append(f"rows[{offset}].{field} 展开长度必须是 80，实际为 {total}")
            heights = cls._decode_rle_row(row_item.get("height_rle"))
            regions = cls._decode_rle_row(row_item.get("region_rle"))
            if heights is None or regions is None:
                continue
            unowned = [col for col in range(80) if heights[col] > 0 and regions[col] <= 0]
            unknown = sorted({regions[col] for col in range(80) if regions[col] > 0 and regions[col] not in known_regions})
            if unowned:
                sample = ", ".join(f"col={col}" for col in unowned[:6])
                errors.append(
                    f"row {expected_row} 有 {len(unowned)} 个 height>0 的点 region_id 为 0（{sample}...）；"
                    "必须给这些点分配 manifest 中已定义的区域 id，或把高度降为 0"
                )
            if unknown:
                errors.append(
                    f"row {expected_row} 引用了 manifest 未定义的 region_id {unknown[:10]}；"
                    "只能使用 manifest.regions 中已定义的 id"
                )
        return errors[:30]

    def review_tactile_candidate(
        self,
        image_data_url: str,
        preview_data_url: str,
        *,
        candidate_summary: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_image_data_url(image_data_url)
        self._validate_image_data_url(preview_data_url)
        model_image_data_url = self._model_image_data_url(image_data_url)
        return self._structured_chat_retry([
            {"role": "system", "content": TACTILE_AGENT_V4_REVIEW_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": model_image_data_url}},
                    {"type": "image_url", "image_url": {"url": preview_data_url}},
                    {
                        "type": "text",
                        "text": "候选帧摘要：\n" + json.dumps(candidate_summary, ensure_ascii=False),
                    },
                ],
            },
        ], max_tokens=800, attempts=2)

    def understand_scene_v5(self, image_data_url: str) -> dict[str, Any]:
        """V5 stage 1: whole-image understanding and expression purpose."""
        self._validate_image_data_url(image_data_url)
        model_image_data_url = self._model_image_data_url(image_data_url)
        return self._structured_chat_retry([
            {"role": "system", "content": TACTILE_AGENT_V5_UNDERSTAND_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": model_image_data_url}},
                    {"type": "text", "text": TACTILE_AGENT_V5_UNDERSTAND_USER},
                ],
            },
        ], max_tokens=2200, attempts=2)

    def generate_sparse_candidate(
        self,
        image_data_url: str,
        *,
        understanding: dict[str, Any],
        content_rect: dict[str, int],
        candidate_number: int,
        user_instruction: str = "",
        feedback: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """V5 stage 2: one-shot global sparse-runs design for the full frame."""
        self._validate_image_data_url(image_data_url)
        model_image_data_url = self._model_image_data_url(image_data_url)
        feedback_text = "当前没有协议错误或评审意见。"
        if feedback:
            feedback_text = (
                "上一候选返回的精确问题如下。请在新候选中自行决定如何整体修正，"
                "不要只局部挪动被点名的点段：\n" + json.dumps(feedback, ensure_ascii=False)
            )
        user_text = (
            TACTILE_AGENT_V5_DESIGN_USER
            .replace("{understanding}", json.dumps(understanding, ensure_ascii=False))
            .replace("{content_rect}", json.dumps(content_rect, ensure_ascii=False))
            .replace("{candidate_number}", str(candidate_number))
            .replace("{user_instruction}", user_instruction.strip() or "无额外要求，按图片自主设计。")
            .replace("{feedback}", feedback_text)
        )
        return self._structured_chat_retry([
            {"role": "system", "content": TACTILE_AGENT_V5_DESIGN_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": model_image_data_url}},
                    {"type": "text", "text": user_text},
                ],
            },
        ], max_tokens=16000, attempts=2)

    def blind_read_matrix(self, matrix_image_data_url: str) -> dict[str, Any]:
        """V5 blind read: interpret the black-white matrix with no original image.

        This runs in a fully isolated context: the reader never sees the source
        image, the scene summary or the generator's notes.
        """
        self._validate_image_data_url(matrix_image_data_url)
        return self._structured_chat_retry([
            {"role": "system", "content": TACTILE_AGENT_V5_BLIND_READ_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": matrix_image_data_url}},
                    {"type": "text", "text": TACTILE_AGENT_V5_BLIND_READ_USER},
                ],
            },
        ], max_tokens=1400, attempts=2)

    def critic_review_v5(
        self,
        image_data_url: str,
        review_page_data_url: str,
        *,
        blind_read: dict[str, Any],
        metrics: dict[str, Any],
        design_notes: str,
        user_instruction: str = "",
    ) -> dict[str, Any]:
        """V5 Critic: fresh isolated context, scores the real rendered page."""
        self._validate_image_data_url(image_data_url)
        self._validate_image_data_url(review_page_data_url)
        model_image_data_url = self._model_image_data_url(image_data_url)
        user_text = (
            TACTILE_AGENT_V5_CRITIC_USER
            .replace("{blind_read}", json.dumps(blind_read, ensure_ascii=False))
            .replace("{metrics}", json.dumps(metrics, ensure_ascii=False))
            .replace("{design_notes}", (design_notes or "").strip() or "（无）")
            .replace("{user_instruction}", user_instruction.strip() or "无")
        )
        return self._structured_chat_retry([
            {"role": "system", "content": TACTILE_AGENT_V5_CRITIC_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": model_image_data_url}},
                    {"type": "image_url", "image_url": {"url": review_page_data_url}},
                    {"type": "text", "text": user_text},
                ],
            },
        ], max_tokens=2600, attempts=2)

    def synthesize_speech(self, text: str) -> tuple[bytes, str] | None:
        """Best-effort OpenAI-compatible TTS; callers keep browser TTS fallback."""
        if not self.credential or not text.strip():
            return None
        model = os.getenv("QWEN_TTS_MODEL", "qwen-audio-3.0-tts-plus").strip()
        voice = os.getenv("QWEN_TTS_VOICE", "Cherry").strip()
        payload = {
            "model": model,
            "input": text.strip(),
            "voice": voice,
            "response_format": "mp3",
        }
        request = urllib.request.Request(
            self.base_url + "/audio/speech",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer " + self.credential,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        try:
            with self._open_with_retry(request, min(self.timeout_seconds, 60), attempts=1) as response:
                return response.read(), response.headers.get_content_type() or "audio/mpeg"
        except Exception:
            return None
