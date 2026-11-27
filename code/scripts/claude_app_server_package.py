"""Assemble the standalone Claude daemon and its registration-read dependencies."""
from pathlib import Path
import hashlib
import json
import re
import os
import subprocess
import tempfile
import shutil
import sys


MODULES = {
    "management_portal": (
        "claude_code_daemon", "claude_provider_catalog", "claude_input_media", "claude_tool_bridge",
        "claude_account_usage", "claude_message_identity", "claude_runtime", "ai_provider_registry", "model_api_store",
        "model_api_wire", "model_api_stream", "model_api_bridge",
        "provider_transport_diagnostics", "resident_shell_environment", "jev_provider",
    ),
    "management_portal/execution": ("account_registrations", "account_hosting", "model_api_protocols", "provider_failures"),
    "management_portal/hatg": ("contracts",),
    "management_common": (
        "provider_subagent_runs", "domain_database", "portal_database", "postgres_database",
        "instance_manifest", "service_components", "secret_environment", "configuration_files", "distribution_targets", "code_environment", "release_dependencies", "loaded_modules", "host_instances", "portal_paths", "provider_account_paths", "operation_errors",
        "file_permissions", "filesystem_paths", "secure_paths", "format_tags", "product_environment", "account_concurrency",
        "site_config", "portal_network", "file_lock", "local_ipc",
    ),
}


def build_package(source: Path, destination: Path, version: str | None = None) -> None:
    for package, modules in MODULES.items():
        target = destination / package
        target.mkdir(parents=True, exist_ok=True)
        # Keep Web/Execution service import hooks out of the standalone daemon.
        (target / "__init__.py").write_text('"""Installed Claude adapter runtime."""\n')
        for module in modules:
            shutil.copyfile(source / package / f"{module}.py", target / f"{module}.py")
    if version is None:  # Compatibility for the legacy, offline program relocation.
        version = (source / "management_portal/claude_app_server_version.txt").read_text().strip()
    (destination / "VERSION").write_text(version + "\n")


def stage_release(source: Path, directory: Path, sdk: Path, sdk_version: str, *, expected_cli: str | None = None) -> Path:
    """Validate adapter and SDK together before making their release selectable."""
    from management_portal.claude_runtime import provider_version, PROVIDER_REVISION
    from management_portal.execution.provider_updates import venv_python
    from packaging.version import Version

    sdk = sdk.resolve(strict=True)
    if sdk.parent != directory.resolve() or str(Version(sdk_version)) != sdk_version:
        raise ValueError("Expected a versioned SDK in the provider directory")
    with tempfile.TemporaryDirectory(prefix=".claude-provider-", dir=directory) as tmp:
        candidate = Path(tmp) / "package"
        build_package(source, candidate, "candidate")
        (candidate / "SDK_DIRECTORY").write_text(sdk.name + "\n")
        probe = subprocess.run([str(venv_python(sdk)), "-I", "-B", "-c",
                        "import sys; sys.path.insert(0, sys.argv[3]); "
                        "from management_portal import claude_code_daemon as daemon; "
                        "import claude_agent_sdk; assert claude_agent_sdk.__version__ == sys.argv[1]; "
                        "assert daemon.APP_SERVER_VERSION == sys.argv[2]; "
                        "assert daemon.APP_SERVER_PROTOCOL_VERSION == 1; "
                        "from management_portal.claude_runtime import native_cli_path; "
                        "import subprocess; print(subprocess.check_output([native_cli_path(), '--version'], text=True).strip())",
                        sdk_version, "candidate", str(candidate)], check=True, timeout=30,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cli = re.fullmatch(r"(\S+) \(Claude Code\)", probe.stdout.strip())
        if not cli:
            raise ValueError("Unexpected Claude Code version response")
        cli_version = str(Version(cli[1]))
        if expected_cli is not None and cli_version != expected_cli:
            raise ValueError("Bundled Claude Code differs from the advertised release")
        version = provider_version(cli_version)
        target = directory / f"claude-provider-{version}"
        (candidate / "VERSION").write_text(version + "\n")
        components = {"agent_sdk": sdk_version, "claude_code": cli_version, "revision": PROVIDER_REVISION}
        from management_portal.execution.provider_session_features import MARKER
        (candidate / MARKER).touch()
        (candidate / "COMPONENTS.json").write_text(json.dumps(components) + "\n")
        (candidate / ".portal-validated").write_text(version + "\n")
        try:
            candidate.rename(target)
        except OSError:
            if not target.is_dir():
                raise

            def contents(root):
                return {p.relative_to(root): hashlib.sha256(p.read_bytes()).digest()
                        for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}

            if contents(candidate) != contents(target):
                raise ValueError("Refusing to replace immutable Claude provider; bump PROVIDER_REVISION")
    return target


if __name__ == "__main__":
    build_package(Path(sys.argv[1]), Path(sys.argv[2]))
