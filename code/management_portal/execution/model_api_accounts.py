"""Provision the compatible execution routes of a saved Model API.

This is a configuration write, never a model call or a runtime installation.
Existing routes (including disabled ones) and their scheduler identities stay put.
"""
from hashlib import sha256

from management_portal.ai_provider_registry import AIProviderRegistry
from . import account_registrations
from .provider_catalog import AGENT_PROVIDERS


def check_identity(api_id):
    if not account_registrations.IDENTIFIER.fullmatch(api_id):
        raise ValueError("api_id 只允许小写字母、数字、下划线和短横线（最多 128 字符）")
    row = account_registrations.registration(api_id)
    if row and row["config"].get("api_id") != api_id:
        raise ValueError(f"API route identity already occupied: {api_id}")


def ensure_routes(entry, actor):
    if not AIProviderRegistry.supports_model_api(entry):
        return []
    api_id = entry["id"]
    check_identity(api_id)
    protocols = AIProviderRegistry.harness_protocols(entry)
    providers = ["provider_api", *(p.provider for p in AGENT_PROVIDERS.values()
                                  if p.api_protocol and p.api_protocol in protocols)]
    result = []
    # Serialize provisioning for this API; another API cannot take a generated
    # name silently because create_registration also owns its identity lock.
    with account_registrations.database().transaction("api-routes:" + api_id):
        rows = account_registrations.registrations()
        for provider in providers:
            existing = [row for row in rows if row["config"].get("api_id") == api_id
                        and (row["provider"] == provider or (provider == "provider_api" and row["account_id"] == api_id))]
            if existing:
                result.extend(existing)
                continue
            name = api_id if provider == "provider_api" else f"{api_id}-{provider}"
            if len(name) > 128:
                name = name[:110] + "-" + sha256(name.encode()).hexdigest()[:16]
            occupied = next((row for row in rows if row["account_id"] == name), None)
            if occupied and provider != "provider_api":
                name = name[:110] + "-" + sha256((api_id + ":" + provider).encode()).hexdigest()[:16]
                occupied = next((row for row in rows if row["account_id"] == name), None)
            if occupied:
                # Direct calls use the API ID as their canonical route; refusing
                # a collision is safer than binding that ID to someone else's key.
                raise ValueError(f"API route identity already occupied: {name}")
            row = account_registrations.create_registration({
                "account_id": name, "provider": provider, "kind": "api_profile",
                "config": {"api_id": api_id}, "max_concurrency": 1,
            }, actor)
            if row is None:
                raise ValueError(f"API route registration changed concurrently: {name}")
            rows.append(row)
            result.append(row)
    return result
