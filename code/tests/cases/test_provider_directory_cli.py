"""Offline discovery stays usable through Windows legacy-codepage pipes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("encoding", ["cp1252", "gbk"])
@pytest.mark.parametrize("provider,marker,variable", [
    ("codex", "auth.json", "CODEX_HOME"),
    ("claude", ".credentials.json", "CLAUDE_CONFIG_DIR"),
    ("antigravity", "oauth_creds.json", "GEMINI_HOME"),
    ("kimi", "config.toml", "KIMI_CODE_HOME"),
])
def test_cli_json_roundtrips_unicode_paths_with_legacy_stdout(tmp_path, encoding, provider, marker, variable):
    home = tmp_path / "中文 account 🧪"
    home.mkdir()
    (home / marker).write_text("DO_NOT_READ_CREDENTIALS", encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts/discover_provider_account_directories.py"
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
           variable: str(home), "PYTHONUTF8": "0", "PYTHONIOENCODING": encoding}
    result = subprocess.run([sys.executable, str(script), "--provider", provider],
                            env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout.decode(encoding))
    candidate = next(row for row in record["candidates"] if row["path"] == str(home))
    assert candidate["provider"] == provider and candidate["login_state"] == "unverified"
    assert marker in candidate["evidence"]
    assert "DO_NOT_READ_CREDENTIALS" not in result.stdout.decode(encoding)
