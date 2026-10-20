"""How Portal checks, stages and switches the shared OpenCode runtime (``ProviderRuntime``)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from management_portal import opencode_provider_catalog as catalog
from management_portal.execution import provider_updates as updates
from management_portal.execution.provider_updates import ProviderRuntime
from management_common.process_runtime import run_process


def agent_models(binary: Path, service: str, env: dict[str, str] | None = None, *, free: bool = False) -> list[dict]:
    """A service's active models that can call tools (``free``: only those that cost nothing).

    As ``opencode models <service> --verbose`` lists them.
    """

    text = updates.run_command(str(binary), "models", service, "--verbose", timeout=120, env=env)
    decoder, position, models = json.JSONDecoder(), 0, []
    while (start := text.find("{", position)) >= 0:
        row, position = decoder.raw_decode(text, start)
        cost = row.get("cost") or {}
        if row.get("status", "active") != "active" or (free and (cost.get("input") or cost.get("output"))):
            continue
        if not (row.get("capabilities") or {}).get("toolcall"):
            continue
        models.append({"id": row["id"], "name": row.get("name") or row["id"],
                       "context": int((row.get("limit") or {}).get("context") or 0),
                       "variants": sorted(row.get("variants") or {})})
    return models


class OpenCodeRuntime(ProviderRuntime):
    PACKAGE = "opencode-ai"

    def available(self) -> tuple[str, ...]:
        return (updates.npm_version(self.PACKAGE, "latest"),)

    def installed(self) -> tuple[str, ...]:
        return (updates.linked_version(self.link(), "opencode-"),)

    def stage(self, versions: tuple[str, ...]) -> Path:
        (version,) = versions
        root = updates.RUNTIMES / f"opencode-{version}"
        binary = updates.npm_binary(root, "opencode")
        marker = root / ".portal-validated"
        if not marker.is_file():
            updates.run_command("npm", "install", "--global", "--prefix", str(root), "--omit=dev", "--no-audit",
                                "--no-fund", f"{self.PACKAGE}@{version}", timeout=1200)
        # OpenCode runs in the directories Portal's sessions give it, never the host user's.
        with tempfile.TemporaryDirectory(prefix="opencode-stage-") as scratch:
            env = {**os.environ, "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": scratch,
                   "XDG_STATE_HOME": scratch, "OPENCODE_DISABLE_AUTOUPDATE": "1"}
            if updates.run_command(str(binary), "--version", timeout=20, env=env) != version:
                raise RuntimeError("OpenCode staged version mismatch")
            # OpenCode prints its help on stderr.
            served = run_process([str(binary), "serve", "--help"], text=True, capture_output=True, timeout=20,
                                 check=True, env=env)
            if "--port" not in served.stdout + served.stderr:
                raise RuntimeError("OpenCode server contract changed")
            staged = {"version": version, "models": agent_models(binary, catalog.ZEN, env, free=True)}
            if not staged["models"]:
                raise RuntimeError("OpenCode lists no free models")
            for service in catalog.SERVICES.values():
                # OpenCode lists a service's models only when it has a key; listing sends no request.
                staged[service.name] = agent_models(binary, service.name, {**env, service.key_variable: "listing-only"})
        (root / "models.json").write_text(json.dumps(staged, indent=1) + "\n")
        marker.write_text(version + "\n")
        return root

    def link(self) -> Path:
        return updates.RUNTIMES / "opencode-current"
