from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from management_portal.test_framework.runner_ids import TAU_RUNNER_ID


@dataclass(frozen=True)
class RunnerDescriptor:
    id: str
    label: str
    implementation_kind: str
    capabilities: tuple[str, ...]
    available: bool = True
    reason: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "implementation_kind": self.implementation_kind,
            "capabilities": list(self.capabilities),
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ProfileDescriptor:
    id: str
    label: str
    description: str
    risk: str

    def public(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "risk": self.risk,
        }


PROFILES = (
    ProfileDescriptor("local.fixture", "本地测试数据", "受控本地数据与服务。", "isolated"),
    ProfileDescriptor("live.controlled", "Live · 受控账号", "在显式选择的本站实例上使用受控测试账号。", "live-account"),
)


class RunnerRegistry:
    """Owns runner identity and declared capability; not Test Case classification."""

    def __init__(
        self,
        *,
        tau_available: bool = False,
        tau_reason: str = "尚未配置 test-actor 浏览器运行配置。",
    ) -> None:
        self._runners = {
            item.id: item
            for item in (
                RunnerDescriptor("pytest", "pytest", "python-node", ("start", "status", "result", "cancel", "stream-output")),
                RunnerDescriptor(
                    TAU_RUNNER_ID,
                    "TAU · Agent 真实使用",
                    "natural-language-task",
                    ("resident-task", "status", "result", "conversation"),
                    available=tau_available,
                    reason="" if tau_available else tau_reason,
                ),
            )
        }
        self._profiles = {item.id: item for item in PROFILES}

    def refresh_tau(self, *, available: bool, reason: str) -> None:
        self._runners[TAU_RUNNER_ID] = replace(
            self._runners[TAU_RUNNER_ID], available=available,
            reason="" if available else reason,
        )

    def runner(self, runner_id: str) -> RunnerDescriptor:
        try:
            return self._runners[str(runner_id or "").strip()]
        except KeyError as exc:
            raise KeyError(f"未知 Runner：{runner_id}") from exc

    def profile(self, profile_id: str) -> ProfileDescriptor:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise KeyError(f"未知运行配置：{profile_id}") from exc

    def public(self) -> dict[str, Any]:
        return {
            "runners": [item.public() for item in self._runners.values()],
            "profiles": [item.public() for item in self._profiles.values()],
        }
