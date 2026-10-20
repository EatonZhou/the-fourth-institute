from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import canonical_record, canonical_resolution

if TYPE_CHECKING:
    from ..service import ResourceService


def install_resolver(service: "ResourceService") -> None:
    from management_portal import device_identity_backend

    service.register_resolver(
        "device",
        lambda address, **context: canonical_resolution(
            device_identity_backend.resolve_device_address(address)
        ),
    )


def install_mutation_provider(service: "ResourceService") -> None:
    from management_portal import device_identity_backend

    def register(
        payload: dict[str, Any],
        *,
        actor: str = "",
        automatic: bool = False,
    ) -> dict[str, Any]:
        del actor, automatic
        record = device_identity_backend.register_device(
            payload,
        )
        return canonical_record(str(record.get("device_id") or ""), record)

    def unregister(
        resource_id: str,
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        result = device_identity_backend.unregister_device(
            resource_id,
        )
        return canonical_record(resource_id, result)

    service.register_mutation_provider(
        "device",
        register=register,
        unregister=unregister,
    )




def install_action_provider(service: "ResourceService") -> None:
    from management_portal import device_identity_backend

    def test_ssh(
        resource_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        return device_identity_backend.test_ssh_connection(
            resource_id,
            timeout_seconds=payload.get("timeout_seconds", 5),
        )

    def save_ssh_credential(
        resource_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        access = device_identity_backend.effective_ssh_access(
            resource_id,
        )
        via_resource_id = str(access["via_resource_id"])
        result = device_identity_backend.save_device_ssh_password(
            via_resource_id,
            str(payload.get("password") or ""),
        )
        return {
            "resource_id": resource_id,
            "via_resource_id": via_resource_id,
            "credential_saved": bool(result["credential_saved"]),
        }

    def delete_ssh_credential(
        resource_id: str,
        _payload: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        del actor
        access = device_identity_backend.effective_ssh_access(
            resource_id,
        )
        via_resource_id = str(access["via_resource_id"])
        result = device_identity_backend.delete_device_ssh_password(via_resource_id)
        return {
            "resource_id": resource_id,
            "via_resource_id": via_resource_id,
            "credential_saved": bool(result["credential_saved"]),
            "deleted": bool(result["deleted"]),
        }

    service.register_action_provider(
        "device",
        {
            "ssh.test": test_ssh,
            "ssh.credential.save": save_ssh_credential,
            "ssh.credential.delete": delete_ssh_credential,
        },
    )
