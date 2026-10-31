"""The installed daemon must read registrations without importing the source tree."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def test_packaged_claude_runtime_imports_dependencies_without_source_tree(tmp_path):
    from scripts.claude_app_server_package import build_package

    code = """
import importlib
from pathlib import Path
import sys

package = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(package))
for name in (
    'management_common.file_permissions',
    'management_common.format_tags',
    'management_common.product_environment',
    'management_common.site_compatibility',
    'management_common.site_config',
    'management_common.portal_network',
    'management_common.file_lock',
    'management_common.local_ipc',
    'management_portal.execution.model_api_protocols',
    'management_portal.jev_provider',
):
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(package), name
print('runtime dependencies imported from standalone package')
"""
    with TemporaryDirectory(dir=tmp_path) as directory:
        package = Path(directory) / "runtime"
        build_package(Path(__file__).resolve().parents[2], package)
        result = subprocess.run([sys.executable, "-I", "-c", code, str(package)],
                                cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "runtime dependencies imported from standalone package"


def test_packaged_claude_catalog_reads_registration_without_model_api(tmp_path):
    from management_portal.execution.account_registrations import create_registration
    from scripts.claude_app_server_package import build_package

    registration = create_registration({"account_id": "claude_packaged", "provider": "claude",
                         "kind": "subscription_login", "config": {
                             "default_model": "sonnet", "models": [{"id": "sonnet"}]}}, "test")
    package = tmp_path / "runtime"
    build_package(Path(__file__).resolve().parents[2], package)
    code = """
import json
from management_portal.claude_provider_catalog import profile
from management_portal import model_api_store
assert all(row['id'] != 'claude_packaged' for row, _ in model_api_store.apis())
entry = profile('claude_packaged')
print(json.dumps({key: entry[key] for key in ('id', 'kind', 'config_dir', 'default_model')}))
"""
    env = {**os.environ, "PYTHONPATH": str(package),
           "PORTAL_PATHS_CONFIG": str(tmp_path / "no-site-config.json"),
           "PORTAL_ACCOUNT_HOST_HOME": str(tmp_path / "accounts")}
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "id": "claude_packaged", "kind": "subscription_login", "default_model": "sonnet",
        "config_dir": registration["config"]["account_dir"]}
