"""The standalone daemon must read the installed instance's path configuration."""

import json
import os
from pathlib import Path
import subprocess
import sys

from management_common.instance_config import STATE_SCHEMA, configuration_digest
from scripts.claude_app_server_package import build_package


def test_standalone_package_resolves_installed_paths_without_source_tree(tmp_path):
    instance = tmp_path / "instance"
    (instance / "config").mkdir(parents=True)
    roots = {key: str(instance / key) for key in (
        "share_root", "staging_root", "instances_root", "accounts_root", "secrets_dir",
    )}
    target = {"install_root": str(instance), "data_roots": roots}
    locator = instance / "config" / "target.json"
    locator.write_text(json.dumps(target))
    (instance / "installation.json").write_text(json.dumps({
        "schema": STATE_SCHEMA, "configuration_sha256": configuration_digest(target),
    }))
    package = tmp_path / "package"
    build_package(Path(__file__).resolve().parents[2], package)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "from management_common.portal_paths import path; print(path('staging_root'))",
         str(package)],
        cwd=tmp_path, env={**os.environ, "PORTAL_PATHS_CONFIG": str(locator)},
        text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == roots["staging_root"]
