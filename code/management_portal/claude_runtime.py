"""Resolve the CLI owned by the active Claude Agent SDK runtime."""
from pathlib import Path
import os
import sys


# Increment for changes to the packaged Portal adapter, including its dependencies.
PROVIDER_REVISION = 2


def provider_version(cli_version: str, revision: int = PROVIDER_REVISION) -> str:
    return f"{cli_version}-{revision}" if revision else cli_version


def release_sdk(package: Path, legacy: Path) -> Path:
    """Resolve the immutable release's SDK; pre-bundle packages use the old entry."""
    package = package.resolve()
    marker = package / "SDK_DIRECTORY"
    if not marker.exists() and not package.name.startswith("claude-provider-"):
        return legacy
    name = marker.read_text().strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("Invalid Claude provider SDK directory")
    return package.parent / name


def native_cli_path() -> str:
    # Import lazily: Portal's regular Python environment does not include the SDK.
    import claude_agent_sdk

    executable = "claude.exe" if sys.platform == "win32" else "claude"
    command = Path(claude_agent_sdk.__file__).parent / "_bundled" / executable
    if not command.is_file() or not os.access(command, os.X_OK):
        raise ValueError("Claude SDK 随附的查询程序不可用，请修复 Claude 运行时安装")
    return str(command)
