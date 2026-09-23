from __future__ import annotations

from typing import Any

from multimodal.base import MultimodalProviderError


class VLMCritic:
    """Adapter around the multimodal provider for the review step."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def review(
        self,
        image_data_url: str,
        preview_data_url: str,
        tactile_plan: dict[str, Any],
        metrics: dict[str, Any],
        device_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            raw = self.provider.critic_review(
                image_data_url=image_data_url,
                preview_data_url=preview_data_url,
                tactile_plan=tactile_plan,
                metrics=metrics,
                device_profile=device_profile,
            )
        except MultimodalProviderError:
            raise
        except Exception as exc:  # provider implementation errors must not crash the loop
            raise MultimodalProviderError(f"critic call failed: {exc}") from exc

        decision = str(raw.get("decision", "")).strip().casefold()
        if decision not in ("accept", "revise"):
            decision = "accept"
        return {
            "decision": decision,
            "comments": [str(c) for c in raw.get("comments", []) if c],
            "tactile_plan": raw.get("tactile_plan")
            if isinstance(raw.get("tactile_plan"), dict)
            else None,
            "_meta": raw.get("_meta", {}),
        }
