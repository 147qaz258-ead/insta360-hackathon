"""X5 acquisition (plan V5 §2).

主链路: Windows PC + USB + Camera SDK（需要 C++ 绑定，比赛现场联调）。
稳定兜底: X5 USB 文件传输模式（DCIM/Camera01）+ 程序监听新文件。
本模块实现兜底链路，并预留 SDK 触发接口。

验收标准: 按一次快门，电脑端连续稳定 10 次得到对应 360° 图像文件并自动进入 input 目录。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".insp", ".dng"}

# MediaSDK 离线拼接（.insp 双鱼眼 -> 等距柱状全景图）
MEDIASDK_TEST = Path(r"E:\影石Bold Maker挑战赛\影石SDK\win64\bin\MediaSDKTest.exe")
STITCH_SIZE = "3840x1920"


def stitch_insp(insp_path: Path, out_jpg: Path, size: str = STITCH_SIZE, params: dict | None = None) -> bool:
    """调用官方 MediaSDKTest.exe 拼接 .insp，输出等距柱状全景 jpg。

    MediaSDKTest 内部用 cv::imread/imwrite，无法读写非 ASCII 路径，
    因此输入/输出都先落到临时 ASCII 路径再搬运。
    params: 影石画质参数（exposure/contrast/... 详见 enhance.PARAM_RANGES），
    colorplus/denoise 默认已开，无需传入。
    """
    import tempfile
    out_jpg.parent.mkdir(parents=True, exist_ok=True)

    def ascii_copy(p: Path) -> Path:
        if str(p).isascii():
            return p
        fd, tmp = tempfile.mkstemp(suffix=p.suffix, prefix="ts_in_")
        os.close(fd)
        shutil.copy2(p, tmp)
        return Path(tmp)

    src = ascii_copy(insp_path)
    fd, tmp_out = tempfile.mkstemp(suffix=".jpg", prefix="ts_stitch_")
    os.close(fd)
    tmp_out = Path(tmp_out)
    cmd = [
        str(MEDIASDK_TEST),
        "-inputs", str(src),
        "-output", str(tmp_out),
        "-output_size", size,
        "-enable_colorplus",
        "-enable_denoise",
    ]
    for k, v in (params or {}).items():
        if k.startswith("enable_"):
            continue
        cmd += [f"-{k}", str(v)]
    try:
        r = subprocess.run(cmd, cwd=MEDIASDK_TEST.parent, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[stitch] 拼接失败: {e}")
        tmp_out.unlink(missing_ok=True)
        if src != insp_path:
            src.unlink(missing_ok=True)
        return False
    if src != insp_path:
        src.unlink(missing_ok=True)
    ok = r.returncode == 0 and tmp_out.exists() and tmp_out.stat().st_size > 0
    if not ok:
        print(f"[stitch] 拼接失败: {r.stdout[-300:]} {r.stderr[-300:]}")
        tmp_out.unlink(missing_ok=True)
        return False
    shutil.copy2(tmp_out, out_jpg)
    tmp_out.unlink(missing_ok=True)
    print(f"[stitch] {insp_path.name} -> {out_jpg.name} ({out_jpg.stat().st_size // 1024} KB)")
    return True


def prepare_pano(src: Path, input_dir: Path) -> tuple[Path | None, dict]:
    """把相机原始文件处理成 Agent 可用的全景图并放入 input_dir。

    返回 (全景图路径, 处理记录)。.insp 原件另存 input/_raw/ 供画质增强闭环重拼接；
    拼接后自动做地平线校正（L2b 保守策略：找不到可信地平线就保持原图）。
    """
    input_dir.mkdir(parents=True, exist_ok=True)
    meta: dict = {"level": None}
    if src.suffix.lower() == ".insp":
        raw_dir = input_dir / "_raw"
        raw_dir.mkdir(exist_ok=True)
        raw_copy = raw_dir / src.name
        if not raw_copy.exists():
            shutil.copy2(src, raw_copy)
        dst = input_dir / f"{src.stem}.jpg"
        if not stitch_insp(src, dst):
            return None, meta
    else:
        dst = input_dir / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
    from touchsight.panorama.level import level_panorama
    try:
        meta["level"] = level_panorama(dst)
        print(f"[level] {dst.name}: {meta['level']['reason']}")
    except Exception as e:
        meta["level"] = {"leveled": False, "reason": f"校正异常跳过: {e}"}
    return dst, meta


def _is_stable(path: Path, checks: int = 3, interval: float = 0.5, max_wait: float = 30.0) -> bool:
    """文件大小连续 checks 次不变才视为写完（相机写入大文件需要时间）。"""
    deadline = time.monotonic() + max_wait
    try:
        last = path.stat().st_size
    except OSError:
        return False
    stable = 1
    while stable < checks:
        if time.monotonic() > deadline:
            return False
        time.sleep(interval)
        try:
            size = path.stat().st_size
        except OSError:
            return False
        stable = stable + 1 if size == last else 1
        last = size
    return True


class SeenStore:
    """持久化记录每个监听目录已见过的文件名（.watch_seen.json）。

    首次见到某目录时只登记现存文件、不触发处理；
    之后出现的未知文件名一律视为新照片——覆盖"相机拔出拍摄、插回导入"场景，
    同时避免旧文件被重复导入。
    """

    def __init__(self, path: Path):
        self.path = path
        self._dirs: dict[str, list[str]] = {}
        if path.exists():
            try:
                self._dirs = json.loads(path.read_text(encoding="utf-8")).get("dirs", {})
            except (json.JSONDecodeError, OSError):
                self._dirs = {}

    def known(self, folder: Path) -> set[str]:
        return set(self._dirs.get(str(folder), []))

    def is_first_contact(self, folder: Path) -> bool:
        return str(folder) not in self._dirs

    def mark(self, folder: Path, names) -> None:
        key = str(folder)
        seen = self._dirs.setdefault(key, [])
        known = set(seen)
        for n in names:
            if n not in known:
                seen.append(n)
                known.add(n)
        self._save()

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps({"dirs": self._dirs}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class MultiWatcher:
    """同时监听多个目录（X5 DCIM 盘 + 网络摄像头本地保存目录）。

    目录不存在时静默等待，热插拔自动恢复；每轮扫描返回所有新稳定文件。
    回调签名: on_new_files(list[Path]) —— 一批一起给，由上层决定聚合策略。
    """

    def __init__(self, folders: list[Path], on_new_files: Callable[[list[Path]], None],
                 poll_interval: float = 1.0, store: SeenStore | None = None):
        self.folders = folders
        self.on_new_files = on_new_files
        self.poll_interval = poll_interval
        self.store = store or SeenStore(Path(__file__).resolve().parents[2] / ".watch_seen.json")
        self._primed: set[str] = set()
        self._stopped = False
        for folder in self.folders:
            if folder.exists():
                self._prime_folder(folder)

    def stop(self) -> None:
        self._stopped = True

    def _prime_folder(self, folder: Path) -> None:
        """目录出现（启动时或热插拔）：首次接触登记现存文件不处理；已接触过的留待扫描补处理。"""
        key = str(folder)
        self._primed.add(key)
        if self.store.is_first_contact(folder):
            current = [p.name for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
            self.store.mark(folder, current)
            print(f"[watch] 首次监听 {folder}，已登记 {len(current)} 个现存文件（不触发处理）")

    def scan_once(self) -> list[Path]:
        found: list[Path] = []
        for folder in self.folders:
            key = str(folder)
            if not folder.exists():
                continue
            if key not in self._primed:
                self._prime_folder(folder)
            known = self.store.known(folder)
            new_names = []
            for p in sorted(folder.iterdir()):
                if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
                    continue
                if p.name in known:
                    continue
                if _is_stable(p):
                    new_names.append(p.name)
                    found.append(p)
            if new_names:
                self.store.mark(folder, new_names)
        return found

    def run(self):
        print(f"[acquisition] 监听 {len(self.folders)} 个目录: {[str(f) for f in self.folders]}")
        while not self._stopped:
            try:
                found = self.scan_once()
            except OSError as e:
                print(f"[watch] 扫描异常（相机可能拔出）: {e}")
                found = []
            if found:
                self.on_new_files(found)
            time.sleep(self.poll_interval)


class BatchCollector:
    """把短时间窗内到达的照片聚合为一个拍摄批次（连拍/间隔拍摄的 N 个时刻）。

    每来一张重置计时；超过 gap_seconds 没有新照片则关闭批次并回调。
    on_waiting(photos, seconds_left) 在每次重置时回调，用于前端倒计时显示。
    """

    def __init__(self, on_batch: Callable[[list[Path]], None], gap_seconds: float = 8.0,
                 on_waiting: Callable[[list[Path], float], None] | None = None):
        self.on_batch = on_batch
        self.gap_seconds = gap_seconds
        self.on_waiting = on_waiting
        self._photos: list[Path] = []
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def add(self, path: Path):
        with self._lock:
            self._photos.append(path)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.gap_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()
            if self.on_waiting:
                self.on_waiting(list(self._photos), self.gap_seconds)

    def add_all(self, paths: list[Path]):
        for p in paths:
            self.add(p)

    def _flush(self):
        with self._lock:
            photos, self._photos = self._photos, []
            self._timer = None
        if photos:
            self.on_batch(sorted(photos))


class AcquisitionService:
    """监听 X5 文件传输盘 + 网络摄像头本地目录 -> (.insp自动拼接) -> input/。"""

    def __init__(self, watch_dirs: list[Path], input_dir: Path):
        self.watch_dirs = watch_dirs
        self.input_dir = input_dir
        self.input_dir.mkdir(parents=True, exist_ok=True)

    def import_file(self, src: Path) -> Path | None:
        dst, meta = prepare_pano(src, self.input_dir)
        if dst:
            print(f"[acquisition] 新照片进入 input: {dst.name} ({dst.stat().st_size // 1024} KB)")
        return dst

    def import_files(self, files: list[Path]) -> list[Path]:
        out = []
        for f in files:
            dst = self.import_file(f)
            if dst:
                out.append(dst)
        return out

    def watch(self, on_ready_files: Callable[[list[Path]], None]):
        def _on_new(files: list[Path]):
            dsts = self.import_files(files)
            if dsts:
                on_ready_files(dsts)

        MultiWatcher(self.watch_dirs, _on_new).run()
