from __future__ import annotations

from management_common.format_tags import schema_matches

import json
import ipaddress
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from management_common.instance_paths import instance_root


DEFAULT_DEPLOYMENT_REGISTRY_PATH = instance_root() / 'deployments.json'
INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
INSTANCE_KINDS = frozenset({"dev", "release", "exploration"})


@dataclass(frozen=True)
class PortalDeployment:
    instance_id: str
    label: str
    kind: str
    origin: str
    order: int
    build_id: str = ""
    browser_origin: str = ""

    @classmethod
    def from_payload(cls, payload: object) -> "PortalDeployment":
        if not isinstance(payload, dict):
            raise ValueError("deployment instance must be an object")
        instance_id = str(payload.get("id") or "").strip().lower()
        if not INSTANCE_ID_RE.fullmatch(instance_id):
            raise ValueError(f"invalid deployment instance id: {instance_id or '(empty)'}")
        label = str(payload.get("label") or "").strip()
        if not label or len(label) > 80:
            raise ValueError(f"invalid deployment label for {instance_id}")
        kind = str(payload.get("kind") or "").strip().lower()
        if kind not in INSTANCE_KINDS:
            raise ValueError(f"invalid deployment kind for {instance_id}: {kind or '(empty)'}")
        origin = str(payload.get("origin") or "").strip()
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"invalid deployment origin for {instance_id}")
        try:
            origin_ip = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ValueError(
                f"deployment origin for {instance_id} must use a loopback IP literal"
            ) from exc
        if not origin_ip.is_loopback:
            raise ValueError(
                f"deployment origin for {instance_id} must use a loopback IP literal"
            )
        browser_origin = str(payload.get("browser_origin") or origin).strip()
        browser_parsed = urlsplit(browser_origin)
        if (
            browser_parsed.scheme != "http"
            or browser_parsed.hostname != "localhost"
            or browser_parsed.username is not None
            or browser_parsed.password is not None
            or browser_parsed.path not in {"", "/"}
            or browser_parsed.query
            or browser_parsed.fragment
        ):
            if browser_origin != origin:
                raise ValueError(
                    f"deployment browser origin for {instance_id} must use localhost"
                )
        elif browser_parsed.port != parsed.port:
            raise ValueError(
                f"deployment browser origin for {instance_id} must use the backend port"
            )
        try:
            order = int(payload.get("order") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid deployment order for {instance_id}") from exc
        build_id = str(payload.get("build_id") or "").strip()[:240]
        return cls(
            instance_id=instance_id,
            label=label,
            kind=kind,
            origin=origin,
            order=order,
            build_id=build_id,
            browser_origin=browser_origin,
        )

    def public_payload(self) -> dict:
        return {
            "id": self.instance_id,
            "label": self.label,
            "kind": self.kind,
            "build_id": self.build_id,
        }


def load_portal_deployments(
    path: Path = DEFAULT_DEPLOYMENT_REGISTRY_PATH,
) -> tuple[PortalDeployment, ...]:
    """Load the enabled Portal instances from the canonical registry."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("deployment registry must be an object")
    if not schema_matches(payload.get("schema"), "tfi.portal-deployments.v1"):
        raise ValueError("unsupported deployment registry schema")
    raw_instances = payload.get("instances")
    if not isinstance(raw_instances, list):
        raise ValueError("deployment registry instances must be a list")
    items = tuple(
        PortalDeployment.from_payload(item)
        for item in raw_instances
        if isinstance(item, dict) and bool(item.get("enabled", True))
    )
    return PortalDeploymentRegistry._validate(items)


class PortalDeploymentRegistry:
    """Read a hot-reloadable deployment list while preserving the last good snapshot."""

    def __init__(
        self,
        path: Path = DEFAULT_DEPLOYMENT_REGISTRY_PATH,
    ):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._deployments = self._read()
        stat = self.path.stat()
        self._signature: tuple[int, int] | None = (
            int(stat.st_mtime_ns),
            int(stat.st_size),
        )
        self._last_error = ""

    @staticmethod
    def _validate(items: tuple[PortalDeployment, ...]) -> tuple[PortalDeployment, ...]:
        by_id: dict[str, PortalDeployment] = {}
        for item in items:
            if item.instance_id in by_id:
                raise ValueError(f"duplicate deployment instance: {item.instance_id}")
            by_id[item.instance_id] = item
        if not by_id:
            raise ValueError("deployment registry must contain at least one enabled instance")
        return tuple(sorted(by_id.values(), key=lambda item: (item.order, item.label, item.instance_id)))

    def _read(self) -> tuple[PortalDeployment, ...]:
        return load_portal_deployments(self.path)

    def deployments(self) -> tuple[PortalDeployment, ...]:
        with self._lock:
            try:
                stat = self.path.stat()
            except FileNotFoundError as exc:
                self._signature = None
                self._last_error = str(exc)
                return self._deployments
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
            if signature == self._signature:
                return self._deployments
            try:
                deployments = self._read()
            except Exception as exc:
                self._last_error = str(exc)
                self._signature = signature
                return self._deployments
            self._deployments = deployments
            self._signature = signature
            self._last_error = ""
            return deployments

    def public_instances(self) -> list[dict]:
        return [item.public_payload() for item in self.deployments()]

    def error(self) -> str:
        self.deployments()
        with self._lock:
            return self._last_error
