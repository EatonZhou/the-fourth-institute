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
            device_identity_backend.resolve_device_address(address, actor=context.get("actor", ""))
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
        update=lambda resource_id, payload, **context: canonical_record(
            resource_id, device_identity_backend.update_device(resource_id, payload)),
    )




def check_device_ssh(service, resource_id, *, actor, timeout_seconds=15):
    from management_portal import device_identity_backend
    resource_id = service._named_operation_target(resource_id, {"device"}, actor=actor,
                                                   instruction="CheckDeviceSsh")
    report = device_identity_backend.test_ssh_connection(resource_id, timeout_seconds=timeout_seconds)
    # The SSH domain report carries diagnostics; the public instruction has the
    # existing ok/error contract, independent of the device implementation.
    result = {"ok": bool(report["reachable"])}
    if not result["ok"]:
        result["error"] = str(report.get("message") or "SSH connection failed")
    return result


def update_device_ssh_credential(service, resource_id, password, *, actor):
    from management_portal import device_identity_backend
    resource_id = service._named_operation_target(resource_id, {"device"}, actor=actor,
                                                   instruction="UpdateDeviceSshCredential")
    access = device_identity_backend.effective_ssh_access(resource_id)
    via_resource_id = str(access["via_resource_id"])
    result = device_identity_backend.save_device_ssh_password(via_resource_id, password)
    return {"resource_id": resource_id, "via_resource_id": via_resource_id,
            "credential_saved": bool(result["credential_saved"])}


def delete_device_ssh_credential(service, resource_id, *, actor):
    from management_portal import device_identity_backend
    resource_id = service._named_operation_target(resource_id, {"device"}, actor=actor,
                                                   instruction="DeleteDeviceSshCredential")
    access = device_identity_backend.effective_ssh_access(resource_id)
    via_resource_id = str(access["via_resource_id"])
    result = device_identity_backend.delete_device_ssh_password(via_resource_id)
    return {"resource_id": resource_id, "via_resource_id": via_resource_id,
            "credential_saved": bool(result["credential_saved"]), "deleted": bool(result["deleted"])}


def run_device_command(service, resource_id, command, *, actor):
    from management_portal import device_identity_backend
    resource_id = service._named_operation_target(resource_id, {"device"}, actor=actor,
                                                   instruction="RunDeviceCommand")
    return device_identity_backend.run_device_ssh(resource_id, command, actor=actor)
