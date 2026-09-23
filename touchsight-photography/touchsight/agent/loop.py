"""Photography Agent loop (plan V5 §6): OBSERVE → THINK → ACT → SEE → EVALUATE."""
from __future__ import annotations

from pathlib import Path

from touchsight.agent.tools import TOOL_SCHEMAS, Toolset
from touchsight.config import PROJECT_ROOT
from touchsight.storage.runs import RunSession
from touchsight.vlm.providers import ChatResult, VisionModelProvider, image_content, text_content

SKILL_DIR = PROJECT_ROOT / "skills" / "touchsight_photographer"


def load_system_prompt() -> str:
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    rubric = (SKILL_DIR / "photography_rubric.md").read_text(encoding="utf-8")
    return f"{skill}\n\n---\n\n{rubric}"


class PhotographyAgent:
    def __init__(
        self,
        provider: VisionModelProvider,
        toolset: Toolset,
        session: RunSession,
        max_steps: int = 12,
    ):
        self.provider = provider
        self.toolset = toolset
        self.session = session
        self.max_steps = max_steps

    def run(self, intent: str) -> dict:
        messages: list[dict] = [
            {"role": "system", "content": load_system_prompt()},
            {
                "role": "user",
                "content": [
                    text_content(
                        f"用户的拍摄要求：「{intent}」\n\n"
                        "360° 场景已经拍好。请开始工作：先获取全局观察视图理解整个场景，"
                        "然后像摄影师一样主动取景、评估、比较，最终保存一张最好的照片。"
                    )
                ],
            },
        ]
        self.session.log("agent_start", {"intent": intent, "max_steps": self.max_steps})

        idle_rounds = 0
        final_summary = ""
        for step in range(1, self.max_steps + 1):
            result: ChatResult = self.provider.chat(messages, tools=TOOL_SCHEMAS)
            assistant_msg: dict = {"role": "assistant"}
            if result.text:
                assistant_msg["content"] = result.text
            if result.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id or f"call_{step}_{i}",
                        "type": "function",
                        "function": {"name": tc.name, "arguments": __import__("json").dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for i, tc in enumerate(result.tool_calls)
                ]
            messages.append(assistant_msg)
            self.session.log("model_message", {
                "step": step,
                "text": result.text,
                "tool_calls": [{"name": tc.name, "args": tc.arguments} for tc in result.tool_calls],
            })
            print(f"\n[step {step}] {result.text[:300]}")

            if not result.tool_calls:
                idle_rounds += 1
                if idle_rounds >= 2:
                    final_summary = result.text
                    break
                messages.append({
                    "role": "user",
                    "content": [text_content("请继续：调用工具取景/比较/保存，或在确认无法改进时调用 save_image 结束。")],
                })
                continue
            idle_rounds = 0

            for tc in result.tool_calls:
                text, images = self.toolset.execute(tc.name, tc.arguments)
                self.session.log("tool_result", {
                    "step": step, "name": tc.name,
                    "text": text, "images": [str(p) for p in images],
                })
                content = [text_content(f"[{tc.name}] {text}")]
                content.extend(image_content(p) for p in images)
                messages.append({"role": "user", "content": content})
                print(f"  -> {tc.name}({tc.arguments}): {text[:120]}")

            if self.toolset.final_candidate is not None:
                final_summary = self.toolset.final_reason
                break
        else:
            final_summary = "达到最大步数，未显式保存。"

        self.session.finalize(self.toolset.final_candidate, final_summary)
        return {
            "run_id": self.session.run_id,
            "run_dir": str(self.session.run_dir),
            "final": str(self.toolset.final_candidate) if self.toolset.final_candidate else None,
            "summary": final_summary,
            "candidates": {k: str(v) for k, v in self.toolset.candidates.items()},
        }
