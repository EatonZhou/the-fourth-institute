"""Resolve the CLI owned by the active Claude Agent SDK runtime."""
from pathlib import Path
import os


def native_cli_path() -> str:
    # Import lazily: Portal's regular Python environment does not include the SDK.
    import claude_agent_sdk

    command = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if not command.is_file() or not os.access(command, os.X_OK):
        raise ValueError("Claude SDK 随附的查询程序不可用，请修复 Claude 运行时安装")
    return str(command)
