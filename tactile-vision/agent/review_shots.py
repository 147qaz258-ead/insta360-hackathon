from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

import websockets


class ReviewShotError(RuntimeError):
    pass


_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def resolve_browser_executable() -> str:
    override = os.getenv("TACTILE_CHROME_PATH", "").strip()
    candidates = [override] if override else []
    candidates.extend(_CHROME_CANDIDATES)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    for name in ("chrome.exe", "chrome", "msedge.exe", "msedge", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    raise ReviewShotError(
        "找不到本机 Chrome/Edge；请安装 Chrome 或设置 TACTILE_CHROME_PATH 指向浏览器可执行文件"
    )


def _kill_browser_tree(pid: int, marker: str) -> None:
    """Chrome spawns renderer/GPU children that can survive the main
    process. taskkill /T covers the tree while the root lives; the marker
    sweep catches orphans afterwards because every child's command line
    carries this shot's unique --user-data-dir path."""
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*"
            + marker
            + "*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
        ],
        capture_output=True,
        check=False,
    )


class _CdpSession:
    def __init__(self, ws: websockets.WebSocketClientProtocol):
        self._ws = ws
        self._next_id = 0

    async def command(self, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        self._next_id += 1
        request: dict = {"id": self._next_id, "method": method}
        if params:
            request["params"] = params
        if session_id:
            request["sessionId"] = session_id
        await self._ws.send(json.dumps(request))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=15)
            message = json.loads(raw)
            if message.get("id") != self._next_id:
                continue
            if "error" in message:
                raise ReviewShotError(f"CDP {method} 失败：{message['error']}")
            return message.get("result", {})


async def _capture_cdp(executable: str, url: str, width: int, height: int, timeout_seconds: float, workdir: Path) -> bytes:
    process = subprocess.Popen(
        [
            executable,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--mute-audio",
            f"--window-size={int(width)},{int(height)}",
            "--remote-debugging-port=0",
            f"--user-data-dir={workdir / 'profile'}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port_file = workdir / "profile" / "DevToolsActivePort"
        deadline = time.monotonic() + min(timeout_seconds, 30)
        port: int | None = None
        while time.monotonic() < deadline:
            if port_file.is_file():
                try:
                    port = int(port_file.read_text().splitlines()[0].strip())
                    break
                except (ValueError, IndexError):
                    pass
            if process.poll() is not None:
                raise ReviewShotError(f"无头浏览器启动即退出（exit={process.returncode}）")
            await asyncio.sleep(0.1)
        if port is None:
            raise ReviewShotError("无头浏览器 DevTools 端口 30s 内未就绪")

        version_url = f"http://127.0.0.1:{port}/json/version"
        version = json.loads(urllib.request.urlopen(version_url, timeout=5).read().decode("utf-8"))
        browser_ws = version["webSocketDebuggerUrl"]

        async with websockets.connect(browser_ws, max_size=64 * 1024 * 1024) as ws:
            cdp = _CdpSession(ws)
            target = await cdp.command("Target.createTarget", {"url": "about:blank"})
            attached = await cdp.command(
                "Target.attachToTarget",
                {"targetId": target["targetId"], "flatten": True},
            )
            session_id = attached["sessionId"]
            await cdp.command("Page.enable", session_id=session_id)
            await cdp.command(
                "Emulation.setDeviceMetricsOverride",
                {"width": int(width), "height": int(height), "deviceScaleFactor": 1, "mobile": False},
                session_id=session_id,
            )
            await cdp.command("Page.navigate", {"url": url}, session_id=session_id)

            # CLI --screenshot fires at the load event, before the review
            # page's async fetch/render completes; --virtual-time-budget
            # hangs outright on this page. Waiting for the page's own
            # readiness title is the only deterministic signal.
            deadline = time.monotonic() + timeout_seconds
            while True:
                result = await cdp.command(
                    "Runtime.evaluate",
                    {"expression": "document.title", "returnByValue": True},
                    session_id=session_id,
                )
                title = str(result.get("result", {}).get("value", ""))
                if title == "review-ready":
                    break
                if title == "review-error":
                    raise ReviewShotError(f"审查页自身报错：{url}")
                if time.monotonic() > deadline:
                    raise ReviewShotError(f"等待审查页就绪超时（{timeout_seconds}s）：{url}")
                await asyncio.sleep(0.3)

            shot = await cdp.command("Page.captureScreenshot", {"format": "png"}, session_id=session_id)
            png = base64.b64decode(shot["data"])
            try:
                await cdp.command("Browser.close")
            except (ReviewShotError, asyncio.TimeoutError, websockets.WebSocketException):
                pass
            return png
    finally:
        if process.poll() is None:
            _kill_browser_tree(process.pid, workdir.name)


def capture_page_screenshot(
    url: str,
    *,
    width: int = 1600,
    height: int = 1000,
    timeout_seconds: float = 60,
) -> bytes:
    """Screenshot a page with the local headless browser via CDP.

    The review page must be a real product page (formal PinRenderer); this
    function drives the browser, waits for the page's own readiness signal
    and returns the PNG it captured.
    """
    executable = resolve_browser_executable()
    workdir = Path(tempfile.mkdtemp(prefix="tactile-shot-"))
    try:
        try:
            return asyncio.run(_capture_cdp(executable, url, width, height, timeout_seconds, workdir))
        except ReviewShotError:
            raise
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException, json.JSONDecodeError) as exc:
            raise ReviewShotError(f"无头浏览器截图失败：{exc}") from exc
    finally:
        _kill_browser_tree(-1, workdir.name)
        shutil.rmtree(workdir, ignore_errors=True)


def capture_page_data_url(url: str, **kwargs) -> str:
    png = capture_page_screenshot(url, **kwargs)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
