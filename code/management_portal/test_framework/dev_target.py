from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from management_portal.portal_deployment_registry import (
    DEFAULT_DEPLOYMENT_REGISTRY_PATH,
    PortalDeployment,
    load_portal_deployments,
)
from scripts.portal_dev_generation import read_build_id, read_component_build_ids
from management_portal.test_framework.catalog import ROOT


EXPECTED_INSTANCE = "dev"
EXPECTED_PAGE_FINGERPRINT = "component-v2"
from management_portal.test_framework.compatibility import test_actor

EXPECTED_ACTOR = test_actor()


class DevTargetError(RuntimeError):
    """The canonical DEV cannot prove it is the source and session under test."""

    def __init__(self, message: str, *, details: dict[str, str] | None = None):
        super().__init__(message)
        self.details = dict(details or {})

    @property
    def temporarily_unavailable(self) -> bool:
        return any(
            self.details.get(f"{stage}_error_type") in {
                "TimeoutError", "URLError", "ConnectionError",
                "ConnectionRefusedError", "ConnectionResetError",
            }
            or self.details.get(f"{stage}_http_status") in {"502", "503", "504"}
            for stage in ("health", "page", "identity")
        )


def _transport_error(message: str, stage: str, error: Exception, observed: dict[str, str]) -> DevTargetError:
    # Keep categories only; URLs, cookies and response bodies may contain secrets.
    details = {**observed, f"{stage}_error_type": type(error).__name__}
    if getattr(error, "code", None) is not None:
        details[f"{stage}_http_status"] = str(error.code)
    if getattr(error, "reason", None) is not None:
        details[f"{stage}_reason_type"] = type(error.reason).__name__
    return DevTargetError(message, details=details)


class DevBuildMismatch(DevTargetError):
    """The same DEV is serving a different build; recheck within a bounded window."""


@dataclass(frozen=True)
class VerifiedDevTarget:
    instance_id: str
    origin: str
    expected_build_id: str
    actual_build_id: str
    page_fingerprint: str
    actor: str
    browser_origin: str = ""

    def result_details(self) -> dict[str, str]:
        return {
            "instance": self.instance_id,
            "browser_origin": self.browser_origin or self.origin,
            "expected_build_id": self.expected_build_id,
            "actual_build_id": self.actual_build_id,
            "page_fingerprint": self.page_fingerprint,
            "actor": self.actor,
        }


def resolve_unique_dev(
    registry_path: Path = DEFAULT_DEPLOYMENT_REGISTRY_PATH,
) -> PortalDeployment:
    matches = [
        item
        for item in load_portal_deployments(Path(registry_path))
        if item.instance_id == EXPECTED_INSTANCE and item.kind == EXPECTED_INSTANCE
    ]
    if len(matches) != 1:
        raise DevTargetError(
            "Portal 部署注册表必须且只能包含一个已启用的 DEV 实例。",
            details={"expected_instance": EXPECTED_INSTANCE, "matches": str(len(matches))},
        )
    return matches[0]


HttpGet = Callable[[str, dict[str, str]], bytes]


def _http_get(url: str, headers: dict[str, str]) -> bytes:
    request = Request(url, headers={"Accept": "application/json, text/html", **headers})
    with urlopen(request, timeout=5) as response:
        return response.read()


def _json_payload(raw: bytes, label: str) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DevTargetError(f"当前唯一 DEV 返回的 {label} 响应无效。") from exc
    if not isinstance(payload, dict):
        raise DevTargetError(f"当前唯一 DEV 返回的 {label} 响应无效。")
    return payload


def _cookie_header(storage_state: Path, deployment: PortalDeployment) -> str:
    try:
        payload = json.loads(storage_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevTargetError("test-actor 浏览器存储状态无法读取。") from exc
    cookies = payload.get("cookies") if isinstance(payload, dict) else None
    if not isinstance(cookies, list):
        raise DevTargetError("test-actor 浏览器存储状态中没有 Cookie。")
    hostname = (urlsplit(deployment.origin).hostname or "").casefold()
    values: list[str] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain") or "").lstrip(".").casefold()
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        if name and value and (domain == hostname or hostname.endswith(f".{domain}")):
            values.append(f"{name}={value}")
    if not values:
        raise DevTargetError("test-actor 浏览器存储状态不适用于当前唯一 DEV。")
    return "; ".join(values)


class DevTargetVerifier:
    """Resolve the one canonical DEV and fail closed before a TAU task starts."""

    def __init__(
        self,
        *,
        registry_path: Path = DEFAULT_DEPLOYMENT_REGISTRY_PATH,
        source_root: Path = ROOT,
        http_get: HttpGet = _http_get,
        build_id: Callable[[Path], str] = lambda _root: (
            read_component_build_ids().get("web") or read_build_id()
        ),
    ) -> None:
        self.registry_path = Path(registry_path)
        self.source_root = Path(source_root)
        self.http_get = http_get
        self.build_id = build_id

    def verify(self, storage_state: Path) -> VerifiedDevTarget:
        deployment = resolve_unique_dev(self.registry_path)
        expected_build = self.build_id(self.source_root)
        observed: dict[str, str] = {
            "instance": deployment.instance_id,
            "expected_build_id": expected_build,
        }

        try:
            health = _json_payload(
                self.http_get(f"{deployment.origin}/api/health", {}),
                "health",
            )
        except DevTargetError:
            raise
        except Exception as exc:
            raise _transport_error(
                "AI 真实使用开始前，当前唯一 DEV 的健康检查失败。", "health", exc, observed
            ) from exc
        actual_instance = str(health.get("instance") or "")
        actual_build = str(health.get("build_id") or "")
        observed.update({"actual_instance": actual_instance, "actual_build_id": actual_build})
        if actual_instance != deployment.instance_id:
            raise DevTargetError(
                "当前唯一 DEV 的实例身份与部署注册表不一致。",
                details=observed,
            )
        if not actual_build or not expected_build:
            raise DevTargetError(
                "当前唯一 DEV 没有运行已激活的 DEV generation 构建。", details=observed
            )
        if actual_build != expected_build:
            raise DevBuildMismatch(
                "当前唯一 DEV 没有运行已激活的 DEV generation 构建。", details=observed
            )

        cookie = _cookie_header(Path(storage_state), deployment)
        authenticated = {"Cookie": cookie}
        try:
            self.http_get(f"{deployment.origin}/development?view=test", authenticated)
            # The development projection loads the Test component on demand.
            # Verify the same authenticated template source used by the browser.
            native_views = self.http_get(f"{deployment.origin}/development/native-views?view=test", authenticated)
        except Exception as exc:
            raise _transport_error(
                "无法使用 test-actor 加载当前唯一 DEV 的测试页面。", "page", exc, observed
            ) from exc
        html = str(_json_payload(native_views, "test components").get("html") or "")
        match = re.search(r'data-test-workbench-version=["\']([^"\']+)["\']', html)
        fingerprint = match.group(1) if match else ""
        observed["page_fingerprint"] = fingerprint
        if fingerprint != EXPECTED_PAGE_FINGERPRINT:
            raise DevTargetError(
                "当前唯一 DEV 的测试页面没有提供要求的组件指纹。",
                details=observed,
            )

        try:
            identity = _json_payload(
                self.http_get(f"{deployment.origin}/api/me", authenticated),
                "identity",
            )
        except DevTargetError:
            raise
        except Exception as exc:
            raise _transport_error(
                "当前唯一 DEV 无法验证 test-actor 会话。", "identity", exc, observed
            ) from exc
        actor = str(identity.get("username") or "")
        observed["actor"] = actor
        if actor != test_actor():
            raise DevTargetError(
                "当前唯一 DEV 的会话用户不是 test-actor。", details=observed
            )
        identity_instance = str(identity.get("portal_instance") or deployment.instance_id)
        identity_build = str(identity.get("build_id") or actual_build)
        if identity_instance != deployment.instance_id or identity_build != actual_build:
            observed.update(
                {"identity_instance": identity_instance, "identity_build_id": identity_build}
            )
            error_type = DevTargetError if identity_instance != deployment.instance_id else DevBuildMismatch
            raise error_type(
                "test-actor 身份来自另一个 Portal 构建。", details=observed
            )

        return VerifiedDevTarget(
            instance_id=deployment.instance_id,
            origin=deployment.origin,
            browser_origin=deployment.browser_origin or deployment.origin,
            expected_build_id=expected_build,
            actual_build_id=actual_build,
            page_fingerprint=fingerprint,
            actor=actor,
        )
