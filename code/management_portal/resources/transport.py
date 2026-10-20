from __future__ import annotations

import json
import secrets
import socket
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import Any, Iterable, Mapping

from management_common.resource_identity import get_resource_type_registry
from management_common.service_socket import SERVICE_RESTART_WAIT_SECONDS, connect_service

from .address import AddressParser, ResourceAddress
from .config import resource_rpc_signing_key_file, resource_service_socket
from .errors import ResourceServiceUnavailable, resource_error_from_payload
from .portal import portal_url_for_address
from .rpc_auth import DOMAIN_PROJECTION_METHODS, RPC_ACTOR_METHODS, load_rpc_signing_key, sign_rpc_auth


class ResourceServiceClient:
    """Thin ResourceService RPC client; ID parsing remains a pure local contract."""

    _RPC_GRACE_SECONDS = 5.0
    _INDEX_READY_SECONDS = 30.0

    def __init__(
        self,
        socket_path: Path | str | None = None,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.socket_path = Path(socket_path or resource_service_socket())
        self.timeout = max(0.1, float(timeout))
        self._identity = get_resource_type_registry()
        self._addresses = AddressParser(self._identity)
        self._rpc_signing_key_path = resource_rpc_signing_key_file(
            socket_path=self.socket_path
        )
        self._rpc_signing_key = load_rpc_signing_key(
            required=False,
            path=self._rpc_signing_key_path,
        )

    def _index_read_timeout(self) -> float:
        return max(
            self.timeout,
            self._INDEX_READY_SECONDS + self._RPC_GRACE_SECONDS,
        )

    def _wait_timeout(
        self,
        params: Mapping[str, Any],
        *,
        wait_key: str,
        timeout_key: str,
        default: float,
    ) -> float | None:
        if not bool(params.get(wait_key)):
            return None
        try:
            requested = float(params.get(timeout_key) or default)
        except (TypeError, ValueError):
            requested = default
        return max(self.timeout, requested + self._RPC_GRACE_SECONDS)


    def prepare_permissions(self, *, actor: str) -> dict[str, Any]:
        # Reads every User, Project, Lineage object and indexed resource once; allow it minutes.
        return self._exchange("permission.prepare", {"actor": actor}, timeout=max(self.timeout, 600.0))

    def new_id(self, resource_type: str, existing_ids: Iterable[str] = ()) -> str:
        return self._identity.new_id(resource_type, existing_ids)

    def validate_id(self, resource_id: str, resource_type: str | None = None):
        return self._identity.validate(resource_id, resource_type)

    def is_valid_id(self, resource_id: str, resource_type: str | None = None) -> bool:
        try:
            self.validate_id(resource_id, resource_type)
        except ValueError:
            return False
        return True

    def parse_address(self, address: str) -> ResourceAddress:
        return self._addresses.parse(address)

    @staticmethod
    def portal_url_for_address(address: object, resource_type: object = "") -> str:
        return portal_url_for_address(address, resource_type)

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, MappingABC):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._json_value(item) for item in value]
        return value

    def _exchange(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        expect_content: bool | str = False,
        restart_wait: float = SERVICE_RESTART_WAIT_SECONDS,
    ) -> Any:
        request_id = f"resource-service-{secrets.token_hex(10)}"
        method_name = str(method or "")
        clean_params = self._json_value(params or {})
        from management_common.resource_lifecycle import current_lifecycle

        internal_projection = method_name in DOMAIN_PROJECTION_METHODS
        if not internal_projection and method_name in RPC_ACTOR_METHODS and clean_params.get("actor") and current_lifecycle():
            clean_params["_resource_lifecycle_id"] = current_lifecycle()
        if method_name == "resource.update":
            from management_common.execution_association import current_execution_id

            if current_execution_id():
                clean_params["_execution_id"] = current_execution_id()
        if internal_projection:
            # Internal projections execute as the signing service.
            clean_params.pop("_resource_lifecycle_id", None)
        actor = (
            str(clean_params.pop("actor", "") or "").strip()
            if method_name in RPC_ACTOR_METHODS
            else ""
        )
        signing_key = self._rpc_signing_key
        if signing_key is None:
            signing_key = load_rpc_signing_key(
                required=False,
                path=self._rpc_signing_key_path,
            )
            self._rpc_signing_key = signing_key
        payload = {
            "id": request_id,
            "method": method_name,
            "params": clean_params,
        }
        if signing_key is not None:
            payload["auth"] = sign_rpc_auth(
                signing_key,
                payload["method"],
                clean_params,
                actor=actor,
            )
        elif actor:
            payload["params"]["actor"] = actor
        connection: socket.socket | None = None
        stream = None
        try:
            connection, _path = connect_service(
                self.socket_path,
                timeout=self.timeout if timeout is None else max(0.1, float(timeout)),
                restart_wait=restart_wait,
            )
            connection.sendall(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            stream = connection.makefile("rb")
            raw = stream.readline()
            if not raw:
                raise ResourceServiceUnavailable(
                    "ResourceService closed the connection without a response"
                )
            response = json.loads(raw)
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise ResourceServiceUnavailable(
                    "ResourceService returned an invalid response envelope"
                )
            if response.get("ok"):
                if not expect_content:
                    return response.get("result")
                content = response.get("content")
                if expect_content == "optional" and content is None:
                    return dict(response.get("result") or {})
                if not isinstance(content, dict):
                    raise ResourceServiceUnavailable(
                        "ResourceService returned an invalid content envelope"
                    )
                content_length = max(0, int(content.get("length") or 0))
                data = stream.read(content_length)
                if len(data) != content_length:
                    raise ResourceServiceUnavailable(
                        "ResourceService content stream ended early"
                    )
                return (
                    data,
                    str(content.get("content_type") or "application/octet-stream"),
                    dict(content.get("resolved") or {}),
                )
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise ResourceServiceUnavailable(
                f"ResourceService unavailable at {self.socket_path}: {exc}"
            ) from exc
        finally:
            if stream is not None:
                stream.close()
            if connection is not None:
                connection.close()
        self._raise_remote_error(response.get("error"))

    @staticmethod
    def _raise_remote_error(raw: object) -> None:
        raise resource_error_from_payload(raw)

    def register(
        self,
        resource_type: str,
        payload: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self._exchange(
            "resource.register",
            {"resource_type": resource_type, "payload": dict(payload), **kwargs},
            timeout=self._wait_timeout(
                kwargs,
                wait_key="wait_for_index",
                timeout_key="index_timeout",
                default=15.0,
            ),
        )
        return dict(result or {})

    def update(
        self,
        resource_id: str,
        payload: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self._exchange(
            "resource.update",
            {"resource_id": resource_id, "payload": dict(payload), **kwargs},
            timeout=self._wait_timeout(
                kwargs,
                wait_key="wait_for_index",
                timeout_key="index_timeout",
                default=15.0,
            ),
        )
        return dict(result or {})

    def execution_content_changes(self, execution_id: str, *, actor: str) -> list[dict[str, Any]]:
        return self._exchange("resource.execution_content_changes", {"execution_id": execution_id, "actor": actor})

    def unregister(self, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        result = self._exchange(
            "resource.unregister",
            {"resource_id": resource_id, **kwargs},
            timeout=self._wait_timeout(
                kwargs,
                wait_key="wait_for_index",
                timeout_key="index_timeout",
                default=15.0,
            ),
        )
        return dict(result or {})

    def action(
        self,
        resource_id: str,
        action: str,
        payload: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            requested_timeout = float(
                (payload or {}).get("timeout_seconds") or 0
            )
        except (TypeError, ValueError):
            requested_timeout = 0.0
        action_timeout = max(
            self.timeout,
            requested_timeout + self._RPC_GRACE_SECONDS,
        )
        result = self._exchange(
            "resource.action",
            {
                "resource_id": resource_id,
                "action": action,
                "payload": dict(payload or {}),
                **kwargs,
            },
            timeout=action_timeout,
        )
        return dict(result or {})

    def resolve(self, address: str, **kwargs: Any) -> dict[str, Any]:
        result = self._exchange(
            "resource.resolve",
            {"address": address, **kwargs},
            timeout=self._index_read_timeout(),
        )
        return dict(result or {})

    def read_content(
        self,
        address: str,
        *,
        actor: str = "",
        max_bytes: int = 4 * 1024 * 1024,
    ) -> tuple[bytes, str, dict[str, Any]]:
        return self._exchange(
            "resource.read_content",
            {
                "address": address,
                "actor": actor,
                "max_bytes": int(max_bytes),
            },
            timeout=max(self.timeout, 60.0),
            expect_content=True,
        )

    def open_content(
        self,
        address: str,
        *,
        actor: str = "",
        max_bytes: int = 4 * 1024 * 1024,
    ) -> dict[str, Any] | tuple[bytes, str, dict[str, Any]]:
        return self._exchange(
            "resource.open_content",
            {"address": address, "actor": actor, "max_bytes": int(max_bytes)},
            timeout=max(self.timeout, 60.0),
            expect_content="optional",
        )

    def read_content_range(
        self,
        address: str,
        *,
        start: int,
        end: int,
        actor: str = "",
    ) -> tuple[bytes, str, dict[str, Any]]:
        return self._exchange(
            "resource.read_content_range",
            {
                "address": address,
                "actor": actor,
                "start": int(start),
                "end": int(end),
            },
            timeout=max(self.timeout, 60.0),
            expect_content=True,
        )

    def reference(self, address: str, **kwargs: Any) -> dict[str, Any]:
        result = self._exchange(
            "resource.reference",
            {"address": address, **kwargs},
            timeout=self._index_read_timeout(),
        )
        return dict(result or {})

    def query(self, **kwargs: Any) -> dict[str, Any]:
        result = self._exchange(
            "resource.query",
            kwargs,
            timeout=self._index_read_timeout(),
        )
        return dict(result or {})

    def suggest(self, *, actor: str = "", q: str = "", limit: int = 30, resource_type: str = "") -> dict[str, Any]:
        result = self._exchange(
            "resource.suggest",
            {"actor": actor, "q": q, "limit": limit, "resource_type": resource_type},
            timeout=self._index_read_timeout(),
        )
        return dict(result or {})

    def get(self, resource_id: str, *, actor: str = "") -> dict[str, Any]:
        result = self._exchange(
            "resource.get",
            {"resource_id": resource_id, "actor": actor},
            timeout=self._index_read_timeout(),
        )
        return dict(result or {})


    def sync_collaboration_resource(
        self,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self._exchange(
            "resource.collaboration.sync",
            {"descriptor": dict(descriptor), "actor": "society-service"},
            timeout=max(self.timeout, 60.0),
        )
        return dict(result or {})

    def sync_collaboration_resources(
        self,
        resources: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        result = self._exchange(
            "resource.collaboration.sync-many",
            {"resources": [dict(item) for item in resources], "actor": "society-service"},
            timeout=max(self.timeout, 60.0),
        )
        return dict(result or {})

    def workspace_id_for_path(
        self,
        path: str | Path,
        *,
        preferred_id: str = "",
    ) -> str:
        result = self._exchange(
            "resource.collaboration.workspace_id_for_path",
            {"path": str(path), "preferred_id": preferred_id},
        )
        return str(result or "")

    def capabilities(self, resource_type: str) -> dict[str, Any]:
        result = self._exchange(
            "resource.capabilities",
            {"resource_type": resource_type},
        )
        return dict(result or {})

    def refresh(self, **kwargs: Any) -> dict[str, Any]:
        result = self._exchange(
            "resource.refresh",
            kwargs,
            timeout=self._wait_timeout(
                kwargs,
                wait_key="wait",
                timeout_key="timeout",
                default=60.0,
            ),
        )
        return dict(result or {})

    def status(self, *, timeout: float | None = None) -> dict[str, Any]:
        result = self._exchange("resource.status", timeout=timeout, restart_wait=0)
        return dict(result or {})

    def projection_state(self, adapter: str) -> dict[str, Any]:
        result = self._exchange(
            "resource.projection_state",
            {"adapter": adapter},
        )
        return dict(result or {})

    def notify_source_changed(self, **kwargs: Any) -> dict[str, Any]:
        result = self._exchange(
            "resource.notify_source_changed",
            kwargs,
            timeout=max(
                self.timeout,
                float(kwargs.get("timeout") or 0) + self._RPC_GRACE_SECONDS,
            ),
        )
        return dict(result or {})
