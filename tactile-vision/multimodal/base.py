from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MultimodalProviderError(RuntimeError):
    pass


class MultimodalProvider(ABC):
    @abstractmethod
    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def understand_image(self, image_data_url: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def plan_tactile(self, image_data_url: str, scene_understanding: dict[str, Any], grounded_scene: dict[str, Any] | None = None, device_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def critic_review(self, image_data_url: str, preview_data_url: str, tactile_plan: dict[str, Any], metrics: dict[str, Any], device_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def explain_touch(self, image_data_url: str, scene_summary: str, region_info: dict[str, Any], question: str) -> dict[str, Any]:
        raise NotImplementedError
