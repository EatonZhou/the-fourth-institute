"""Native HATG asset recovery and incremental Function Block drag geometry.

:module: lineage.hatg
"""
import subprocess
from pathlib import Path


def _browser_case(name):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / name)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_assets_recover_from_download_failure():
    _browser_case("hatg_asset_retry.browser.cjs")


def test_drag_updates_only_affected_function_block_boundaries():
    _browser_case("hatg_boundary_drag.browser.cjs")
