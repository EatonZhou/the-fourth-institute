from __future__ import annotations

import os


def portal_instance() -> str:
    return str(os.getenv("PORTAL_INSTANCE", "dev") or "dev").strip()


def nats_url() -> str:
    configured = str(os.getenv("PORTAL_NATS_URL", "") or "").strip()
    if configured:
        return configured
    port = 4223 if portal_instance() == "dev" else 4224
    return f"nats://127.0.0.1:{port}"


def nats_client_name(component: str) -> str:
    clean = str(component or "portal").strip().replace(" ", "-")
    return f"tfi-{portal_instance()}-{clean}"


def nats_enabled() -> bool:
    value = str(os.getenv("PORTAL_NATS_ENABLED", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}
