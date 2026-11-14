"""Bounded, versioned access to a registered Codex account's native defaults.

The native app-server owns config parsing, atomic writes and reloads. Portal
never returns the full configuration, credentials or project config layers.
Changing defaults does not restart the host or alter existing threads.
"""
from __future__ import annotations

import re
from pathlib import Path

from management_portal.hatg.contracts import InstructionError, invalid, not_found


def _connection(account_id):
    from .account_registrations import registration
    from .account_hosting import account_home, codex_socket_path
    from management_portal.codex_app_server_client import client_for_socket

    row = registration(account_id)
    if row is None:
        raise not_found("执行账号不存在")
    if row["provider"] != "codex" or row["kind"] != "subscription_login":
        raise invalid("原生默认配置目前只支持 Codex 订阅账号")
    home = account_home("codex", account_id)
    return client_for_socket(codex_socket_path(account_id)).rpc, home / "config.toml"


def _read(rpc, path, account_id):
    result = rpc.request("config/read", {"includeLayers": True})
    layers = [layer for layer in result.get("layers") or []
              if layer.get("name", {}).get("type") == "user"
              and not layer["name"].get("profile")
              and Path(layer["name"].get("file", "")) == path]
    if len(layers) != 1 or not layers[0].get("version"):
        raise InstructionError("instruction.conflict", "原生账号配置层或版本不可确认，未执行写入")
    layer = layers[0]
    config = layer.get("config") or {}
    effective = result.get("config") or {}
    provider = config.get("model_provider") or "openai"
    return {"account_id": account_id, "model_provider": provider,
            "effective_model_provider": effective.get("model_provider") or "openai",
            "model_providers": sorted((config.get("model_providers") or {}).keys()),
            "version": layer["version"]}


def get_configuration(inv):
    account_id = inv.argument("account_id")
    rpc, path = _connection(account_id)
    return {"configuration": _read(rpc, path, account_id)}


def update_configuration(inv):
    account_id = inv.argument("account_id")
    provider = inv.argument("model_provider")
    if not isinstance(provider, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", provider):
        raise invalid("model_provider 必须是原生 Provider ID")
    expected = inv.expected_version_for(account_id)
    if not isinstance(expected, str) or not expected:
        raise invalid("调用信封 expected_version 必须为原生配置版本")
    rpc, path = _connection(account_id)
    before = _read(rpc, path, account_id)
    if before["version"] != expected:
        raise InstructionError("instruction.version_conflict", "原生配置版本已改变")
    if provider != "openai" and provider not in before["model_providers"]:
        raise invalid("model_provider 未在该账号原生配置中定义")
    if before["model_provider"] == provider and before["effective_model_provider"] == provider:
        return {"configuration": before, "changed": False}
    try:
        written = rpc.request("config/value/write", {
            "keyPath": "model_provider", "value": provider, "mergeStrategy": "replace",
            "filePath": str(path), "expectedVersion": expected,
        })
    except Exception as exc:
        raise InstructionError("instruction.failed", "原生配置写入未获确认；请重新读取版本与实际值，勿盲目重试",
                               commit_state="unknown", details={"account_id": account_id}) from exc
    # A successful native write is already committed even if a subsequent
    # observation fails. Do not mislabel that as a retryable, uncommitted call.
    try:
        after = _read(rpc, path, account_id)
    except Exception as exc:
        raise InstructionError("instruction.projection_pending", "原生配置已写入，回读尚未确认",
                               commit_state="committed", details={"account_id": account_id,
                               "version": written.get("version")}) from exc
    if after["model_provider"] != provider or after["effective_model_provider"] != provider:
        raise InstructionError("instruction.conflict", "原生配置已写入，但被其他配置层覆盖或随后改变",
                               commit_state="committed", details={"account_id": account_id,
                               "version": written.get("version")})
    return {"configuration": after, "changed": True}


HANDLERS = {"GetProviderAccountConfiguration": get_configuration,
            "UpdateProviderAccountConfiguration": update_configuration}
