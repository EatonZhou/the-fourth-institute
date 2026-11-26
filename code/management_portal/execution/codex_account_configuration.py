"""Bounded, versioned access to a registered Codex account's native defaults.

The native app-server owns config parsing, atomic writes and reloads. Portal
never returns the full configuration, credentials or project config layers.
Changing defaults does not restart the host or alter existing threads.
"""
from __future__ import annotations

from pathlib import Path

from management_portal.hatg.contracts import InstructionError, invalid


def _connection(account_id):
    from .account_hosting import account_home, codex_socket_path
    from management_portal.codex_app_server_client import client_for_socket

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


def _get_configuration(account_id, *, include_references):
    rpc, path = _connection(account_id)
    result = _read(rpc, path, account_id)
    if include_references:
        # Native filters and a single result suffice to prove a persisted
        # reference. Never return previews, thread IDs or conversation data.
        sources = ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview",
                   "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"]
        references = {}
        for provider in result["model_providers"]:
            found = False
            for archived in (False, True):
                page = rpc.request("thread/list", {"limit": 1, "modelProviders": [provider],
                                   "sourceKinds": sources, "archived": archived})
                if page.get("data"):
                    found = True
                    break
            references[provider] = {"persisted_reference_found": found}
        result["provider_references"] = references
    return {"configuration": result}


def _update_configuration(account_id, provider, expected):
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


def configuration(row, *, changes=None, expected_version=None, include_references=False):
    if changes is None:
        return _get_configuration(row["account_id"], include_references=include_references)
    return _update_configuration(row["account_id"], changes["model_provider"], expected_version)
