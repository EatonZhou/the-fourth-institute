from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common import instance_paths
from management_common import portal_network
from management_common.instance_paths import instance_root
from management_common.portal_paths import path as portal_path
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config
from management_common.postgres_configuration import postgres_env_file


def site_environment(deployment: dict | None = None) -> dict[str, str]:
    """Shared non-secret site paths used by units and one-off host operations."""
    deployment = read_site_config().get("deployment", {}) if deployment is None else deployment
    configured = (json.loads(instance_paths.CONFIG_PATH.read_text(encoding="utf-8")).get("environment", {})
                  if instance_paths.CONFIG_PATH.exists() else {})
    result = {
        "PORTAL_INSTANCES_ROOT": str(instance_root()),
        "PORTAL_ACCOUNTS_ROOT": str(portal_path("accounts_root")),
        "PORTAL_SECRETS_DIR": str(portal_path("secrets_dir")),
        "PORTAL_NETWORK_CONFIG": str(portal_network.config_path()),
        "PORTAL_SITE_CONFIG": str(resolve_directory(os.environ.get("PORTAL_SITE_CONFIG", str(ROOT / "config/site.json")))),
    }
    if os.environ.get("PORTAL_PATHS_CONFIG"):
        result["PORTAL_PATHS_CONFIG"] = str(resolve_directory(os.environ["PORTAL_PATHS_CONFIG"]))
    egress = os.environ.get("PORTAL_PROVIDER_EGRESS_ENV_FILE", deployment.get("providers", {}).get(
        "egress_env_file", configured.get("PORTAL_PROVIDER_EGRESS_ENV_FILE", "")))
    if egress:
        result["PORTAL_PROVIDER_EGRESS_ENV_FILE"] = str(resolve_directory(egress))
    return result


def _deployment_values(deployment: dict) -> dict[str, str]:
    providers = deployment.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("Deployment sections must be objects")
    source = resolve_directory(deployment.get("source_root", str(ROOT)))
    runtimes = resolve_directory(providers.get("runtime_root", str(portal_path("staging_root") / "provider-runtimes")))
    return {
        "PORTAL_POSTGRES_ENV_FILE": str(postgres_env_file()),
        "PORTAL_STAGING_ROOT": str(portal_path("staging_root")),
        "PORTAL_PROVIDER_RUNTIME_ROOT": str(runtimes),
        "PORTAL_CLAUDE_SOURCE_ROOT": str(resolve_directory(providers.get("claude_source_root", str(source)))),
        "PORTAL_RENDERED_CONFIG_ROOT": str(instance_root() / "systemd"),
    }


def render_unit(source: Path) -> str:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be an object")
    values = _deployment_values(deployment)
    environment = site_environment(deployment)
    for value in (values | environment).values():
        if any(character.isspace() for character in value) or any(character in value for character in "'\"\\%$"):
            raise ValueError("Systemd site path contains unsupported shell characters")
    content = source.read_text(encoding="utf-8")
    egress = environment.get("PORTAL_PROVIDER_EGRESS_ENV_FILE", "")
    content = content.replace("@PORTAL_PROVIDER_EGRESS_DIRECTIVE@", f"EnvironmentFile={egress}" if egress else "")
    if "@PORTAL_CODEX_BINARY@" in content:
        from management_portal.execution.provider_updates import CodexRuntime
        # Rendering can follow a site-config change in the same process.
        # Prefer that site's explicit path over the runtime module's cached
        # deployment, keeping the executable link itself intact.
        configured = deployment.get("providers", {}).get("codex_binary")
        if configured is not None and not isinstance(configured, str):
            raise ValueError("Codex executable must be a path")
        path = Path(configured).expanduser() if configured else None
        binary = str(resolve_directory(str(path.parent)) / path.name) if path else str(CodexRuntime().link())
        if not binary or any(character.isspace() or character in "'\"\\%$" for character in binary):
            raise ValueError("Codex executable is missing or its path cannot be used by systemd")
        content = content.replace("@PORTAL_CODEX_BINARY@", binary)
    for name, value in (values | environment).items():
        content = content.replace(f"@{name}@", value)
    if re.search(r"@PORTAL_[A-Z_]+@", content):
        raise ValueError("Unresolved systemd template placeholder")
    if "[Service]" in content:
        variables = "\n".join(f"Environment={name}={value}" for name, value in environment.items())
        content = content.replace("[Service]", f"[Service]\n{variables}", 1)
        # Later EnvironmentFiles override earlier Provider defaults. The optional
        # file contains no proxy keys until an administrator explicitly saves them.
        start = content.index("[Service]") + len("[Service]")
        end = content.find("\n[", start)
        end = len(content) if end < 0 else end
        directive = f"\nEnvironmentFile=-{environment['PORTAL_NETWORK_CONFIG']}\n"
        content = content[:end].rstrip("\n") + directive + content[end:]
    return content


def install_unit(source: Path, destination: Path) -> None:
    content = render_unit(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
