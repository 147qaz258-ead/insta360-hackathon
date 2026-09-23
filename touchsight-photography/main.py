"""TouchSight CLI 入口。

用法:
  python main.py run <全景图路径> [--intent "帮我们三个人拍合照，塔也留下"] [--provider mock]
  python main.py batch <全景图1> <全景图2> ... [--intent "帮我们拍合照"] [--provider mock]
  python main.py watch [--dcim "F:\\DCIM\\Camera01"] [--intent "..."]
  python main.py gen-test-pano [输出路径]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from touchsight.agent.loop import PhotographyAgent
from touchsight.agent.timing import select_best_moment
from touchsight.agent.tools import Toolset
from touchsight.capture.acquisition import BatchCollector
from touchsight.config import settings
from touchsight.panorama.views import PanoramaViewGenerator
from touchsight.storage.runs import RunSession
from touchsight.vlm.providers import make_provider

DEFAULT_INTENT = "为这个场景拍一张最好的照片"


def run_once(pano_path: Path, intent: str, provider_name: str | None = None) -> dict:
    if provider_name:
        settings.provider = provider_name
    provider = make_provider(settings)
    session = RunSession(settings.runs_dir, intent=intent)
    pano_path = Path(pano_path)
    session.save_panorama(pano_path)
    generator = PanoramaViewGenerator(pano_path)
    toolset = Toolset(generator, session)
    agent = PhotographyAgent(provider, toolset, session, max_steps=settings.max_agent_steps)
    result = agent.run(intent)
    print("\n===== 运行完成 =====")
    print(f"run 目录: {result['run_dir']}")
    print(f"最终成片: {result['final']}")
    print(f"决策说明: {result['summary']}")
    return result


def run_batch(pano_paths: list[Path], intent: str, provider_name: str | None = None) -> dict:
    """批次模式：先跨时刻选最佳一张，再对它运行摄影 Agent。"""
    if provider_name:
        settings.provider = provider_name
    provider = make_provider(settings)
    print(f"\n[batch] 共 {len(pano_paths)} 个时刻，先由 VLM 评选最佳时刻...")
    sel = select_best_moment(provider, pano_paths, intent)
    print(f"[batch] 选中第 {sel['best_index'] + 1} 张: {sel['best_path'].name}")
    print(f"[batch] 理由: {sel['reason']}")
    for i, note in enumerate(sel.get("notes", []), 1):
        print(f"  - 时刻{i}: {note}")
    return run_once(sel["best_path"], intent)


def watch(dcim: Path | None, intent: str, provider_name: str | None = None):
    if provider_name:
        settings.provider = provider_name
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    from touchsight.capture.acquisition import AcquisitionService
    collector = BatchCollector(lambda photos: run_batch(photos, intent), gap_seconds=8.0)

    def on_ready(paths: list[Path]):
        print(f"\n[watch] {len(paths)} 张新全景图就绪，加入拍摄批次（8s 无新片后统一评选）...")
        collector.add_all(paths)

    dirs = [dcim] if dcim else settings.watch_dirs
    print(f"[watch] 监听 {len(dirs)} 个目录，新照片将自动导入并处理:")
    for d in dirs:
        print(f"  - {d}")
    AcquisitionService(dirs, settings.input_dir).watch(on_ready)


def main():
    parser = argparse.ArgumentParser(description="TouchSight X5 视觉摄影智能体")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="对单张全景图运行摄影 Agent")
    p_run.add_argument("pano", type=Path)
    p_run.add_argument("--intent", default=DEFAULT_INTENT)
    p_run.add_argument("--provider", choices=["openai", "mock"], default=None)

    p_batch = sub.add_parser("batch", help="对连拍批次先选最佳时刻，再运行摄影 Agent")
    p_batch.add_argument("panos", type=Path, nargs="+")
    p_batch.add_argument("--intent", default=DEFAULT_INTENT)
    p_batch.add_argument("--provider", choices=["openai", "mock"], default=None)

    p_watch = sub.add_parser("watch", help="监听 X5 DCIM 目录或 input 目录，自动处理新照片")
    p_watch.add_argument("--dcim", type=Path, default=None)
    p_watch.add_argument("--intent", default=DEFAULT_INTENT)
    p_watch.add_argument("--provider", choices=["openai", "mock"], default=None)

    p_gen = sub.add_parser("gen-test-pano", help="生成合成测试全景图")
    p_gen.add_argument("out", type=Path, nargs="?", default=Path("input/test_pano.jpg"))

    args = parser.parse_args()
    if args.cmd == "run":
        if not args.pano.exists():
            print(f"文件不存在: {args.pano}")
            sys.exit(1)
        run_once(args.pano, args.intent, args.provider)
    elif args.cmd == "batch":
        paths = [p for p in args.panos if p.exists()]
        missing = [p for p in args.panos if not p.exists()]
        for p in missing:
            print(f"文件不存在，已跳过: {p}")
        if not paths:
            print("没有可用的全景图")
            sys.exit(1)
        run_batch(paths, args.intent, args.provider)
    elif args.cmd == "watch":
        watch(args.dcim, args.intent, args.provider)
    elif args.cmd == "gen-test-pano":
        from scripts.make_test_pano import generate
        out = generate(args.out)
        print(f"测试全景图已生成: {out}")


if __name__ == "__main__":
    main()
