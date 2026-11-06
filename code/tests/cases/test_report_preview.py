"""Formal report resources use the report view inside shared Preview.

:module: workbench.preview
:design: decision_61e50306cc79
:design: decision_e3ced6d30f6b
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import subprocess
import pytest



def test_formal_report_visual_preview():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'report_preview.browser.cjs')],
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_explanation_manifest_visual_preview():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'home_explanation.browser.cjs'), '--preview-only'],
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_report_wheel_scroll_in_file_preview():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'report_preview.browser.cjs'), '--scroll-only'],
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('scenario', [
    'cold-switch-report', 'cold-switch-explanation', 'cold-switch-display',
    'focus-loading', 'focus-error-retry', 'asset-retry', 'identity-loading', 'validation-switch',
])
def test_report_preview_loading_lifecycle(scenario):
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'report_preview.browser.cjs'),
         '--lifecycle=' + scenario],
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
