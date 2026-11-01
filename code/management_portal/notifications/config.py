from __future__ import annotations

import os


def nats_url() -> str:
    from management_common.instance_manifest import load
    configured = str(os.getenv("PORTAL_NATS_URL", "") or "").strip()
    return configured or f"nats://127.0.0.1:{load().document['network']['nats_port']}"


def nats_client_name(component: str) -> str:
    from management_common.instance_manifest import load
    clean = str(component or "portal").strip().replace(" ", "-")
    return f"tfi-{load().instance_id}-{clean}"


def nats_enabled() -> bool:
    value = str(os.getenv("PORTAL_NATS_ENABLED", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}
