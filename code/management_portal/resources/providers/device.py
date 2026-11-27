from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import canonical_record, canonical_resolution

if TYPE_CHECKING:
    from ..service import ResourceService


# Text fields deliberately retain the owner's str(value or '').strip()
# compatibility. Numeric SSH/GPU fields retain int() compatibility too.
_TEXT_TYPES = ["string", "number", "boolean", "array", "object", "null"]
_ACCESS = {
    "type": ["object", "null"], "description": "连接参数；null 清空。只支持 ssh；配置 SSH 选项时领域要求 host。",
    "additionalProperties": False,
    "properties": {"ssh": {
        "type": ["object", "null"], "description": "SSH 参数；路径合法性和 host 组合由设备领域检查。",
        "additionalProperties": False,
        "properties": {
            **{key: {"type": _TEXT_TYPES, "description": description} for key, description in {
                "host": "SSH host；沿用文本转换，不能以 - 开头。",
                "user": "SSH 用户名；沿用文本转换。",
                "config_path": "主机上的绝对 SSH 配置路径；禁止 .. 和控制字符。",
                "identity_file": "主机上的绝对私钥引用路径；禁止 .. 和控制字符。",
            }.items()},
            "port": {"description": "SSH 端口；int 转换后 1–65535；兼容数字字符串、数值及 true，null/空串表示未设置。",
                     "anyOf": [{"type": "number", "minimum": 1, "exclusiveMaximum": 65536},
                               {"type": "boolean", "const": True}, {"type": "null"},
                               {"type": "string", "pattern": r"^(?:|\s*\+?[0-9]+\s*)$"}]},
        },
    }},
}
_NAME = {"type": _TEXT_TYPES, "description": "设备名称；文本转换并去首尾空白后必须非空。",
         "pattern": r"\S", "not": {"enum": [None, False, 0, [], {}]}}
REGISTER_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "description": "登记设备及 GPU server 的完整库存；拓扑唯一性和连接约束由设备领域在事务中检查。",
    "required": ["name"], "additionalProperties": False,
    "properties": {
        "name": _NAME,
        "device_kind": {"type": _TEXT_TYPES, "description": "设备种类，默认 device；不能单独登记 gpu。",
                        "pattern": r"^\s*(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)*)?\s*$",
                        "not": {"type": "string", "pattern": r"^\s*gpu\s*$"}},
        **{key: {"type": _TEXT_TYPES, "description": description} for key, description in {
            "description": "设备说明；沿用文本转换。", "lifecycle": "生命周期；空值默认 active。",
            "hostname": "主机名；沿用文本转换。", "location": "设备位置；沿用文本转换。",
            "primary_ip": "主 IP；沿用文本转换。", "model": "设备型号；沿用文本转换。",
        }.items()},
        "access_methods": _ACCESS,
        "monitor": {"type": ["object", "null"], "description": "仅 gpu_server；kind 和 expected_gpu_count 由库存生成，提交值会被覆盖。",
                    "additionalProperties": False, "properties": {
                        "kind": {"type": _TEXT_TYPES, "description": "忽略提交值，领域写入 nvidia_smi。"},
                        "expected_gpu_count": {"type": _TEXT_TYPES, "description": "忽略提交值，领域写入 gpus 长度。"},
                        "transport": {"type": _TEXT_TYPES, "description": "local 或 ssh；空值默认 ssh，ssh 必须有 host。",
                                      "pattern": r"^\s*(?:local|ssh)?\s*$"},
                    }},
        "gpus": {"type": "array", "minItems": 1, "description": "gpu_server 必填完整 GPU 库存；索引和 UUID 唯一性由领域检查。",
                 "items": {"type": "object", "required": ["gpu_index", "gpu_uuid", "model"],
                           "additionalProperties": False, "properties": {
                               "gpu_index": {"description": "int 转换后非负；兼容数值、布尔与数字字符串。",
                                             "anyOf": [{"type": "number", "exclusiveMinimum": -1}, {"type": "boolean"},
                                                       {"type": "string", "pattern": r"^\s*[+-]?[0-9]+\s*$"}]},
                               "gpu_uuid": {**_NAME, "description": "GPU 稳定 UUID，文本转换后非空。"},
                               "model": {**_NAME, "description": "GPU 型号，文本转换后非空。"},
                           }}},
    },
    "allOf": [{"if": {"required": ["device_kind"], "properties": {
        "device_kind": {"type": "string", "pattern": r"^\s*gpu_server\s*$", "description": "选择 GPU server 库存分支。"}}},
        "then": {"required": ["gpus"]},
        "else": {"properties": {"monitor": {"description": "仅 gpu_server 接受。", "not": {}},
                                 "gpus": {"description": "仅 gpu_server 接受。", "not": {}}}}}],
}
UPDATE_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "description": "更新名称或 SSH 参数；保留设备领域的拓扑、路径和事务检查。",
    "minProperties": 1, "additionalProperties": False,
    "properties": {"name": _NAME, "access_methods": _ACCESS},
}


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
        register_contract=REGISTER_CONTRACT,
        update_contract=UPDATE_CONTRACT,
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
