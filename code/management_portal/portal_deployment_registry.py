from __future__ import annotations

import ipaddress
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from management_common.instance_manifest import load


INSTANCE_MANIFEST_PATH = load().root / "instance.json"
DEPLOYMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DEPLOYMENT_KINDS = frozenset({"dev", "release", "exploration"})


@dataclass(frozen=True)
class PortalDeployment:
    deployment_id: str
    label: str
    kind: str
    origin: str
    order: int
    build_id: str = ""
    browser_origin: str = ""

    @classmethod
    def from_payload(cls, payload: object) -> "PortalDeployment":
        if not isinstance(payload, dict):
            raise ValueError("deployment must be an object")
        deployment_id = str(payload.get("id") or "").strip().lower()
        if not DEPLOYMENT_ID_RE.fullmatch(deployment_id):
            raise ValueError(f"invalid deployment id: {deployment_id or '(empty)'}")
        label = str(payload.get("label") or "").strip()
        if not label or len(label) > 80:
            raise ValueError(f"invalid deployment label for {deployment_id}")
        kind = str(payload.get("kind") or "").strip().lower()
        if kind not in DEPLOYMENT_KINDS:
            raise ValueError(f"invalid deployment kind for {deployment_id}: {kind or '(empty)'}")
        origin = str(payload.get("origin") or "").strip()
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"invalid deployment origin for {deployment_id}")
        try:
            origin_ip = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ValueError(
                f"deployment origin for {deployment_id} must use a loopback IP literal"
            ) from exc
        if not origin_ip.is_loopback:
            raise ValueError(
                f"deployment origin for {deployment_id} must use a loopback IP literal"
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
                    f"deployment browser origin for {deployment_id} must use localhost"
                )
        elif browser_parsed.port != parsed.port:
            raise ValueError(
                f"deployment browser origin for {deployment_id} must use the backend port"
            )
        try:
            order = int(payload.get("order") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid deployment order for {deployment_id}") from exc
        build_id = str(payload.get("build_id") or "").strip()[:240]
        return cls(
            deployment_id=deployment_id,
            label=label,
            kind=kind,
            origin=origin,
            order=order,
            build_id=build_id,
            browser_origin=browser_origin,
        )

    def public_payload(self) -> dict:
        return {
            "id": self.deployment_id,
            "label": self.label,
            "kind": self.kind,
            "build_id": self.build_id,
        }


def load_portal_deployments(
    path: Path = INSTANCE_MANIFEST_PATH,
) -> tuple[PortalDeployment, ...]:
    """Project enabled Web deployments from the one instance manifest."""
    path = Path(path)
    if path.name != 'instance.json':
        raise ValueError('Deployment discovery requires the instance manifest')
    manifest = load(path.parent)
    items = tuple(PortalDeployment.from_payload({
        'id': name, 'kind': row['kind'], 'label': name.title(),
        'origin': f"http://127.0.0.1:{row['web_port']}",
        'browser_origin': f"http://localhost:{row['web_port']}",
        'order': 0 if name == 'release' else 1,
    }) for name, row in manifest.deployments.items())
    return PortalDeploymentRegistry._validate(items)



class PortalDeploymentRegistry:
    """Read a hot-reloadable deployment list while preserving the last good snapshot."""

    def __init__(
        self,
        path: Path = INSTANCE_MANIFEST_PATH,
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
            if item.deployment_id in by_id:
                raise ValueError(f"duplicate deployment: {item.deployment_id}")
            by_id[item.deployment_id] = item
        if not by_id:
            raise ValueError("deployment registry must contain at least one enabled deployment")
        return tuple(sorted(by_id.values(), key=lambda item: (item.order, item.label, item.deployment_id)))

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

    def public_deployments(self) -> list[dict]:
        return [item.public_payload() for item in self.deployments()]

    def error(self) -> str:
        self.deployments()
        with self._lock:
            return self._last_error
