"""Formal report resources use the report view inside shared Preview.

:module: interface.content
:design: decision_61e50306cc79
:design: decision_e3ced6d30f6b
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import json
import subprocess
from unittest.mock import patch

from management_portal import home_report_http


def test_formal_report_visual_preview():
    # Exercise the actual standalone shell, not a test copy of its dependencies.
    with patch.object(home_report_http.http_responses, '_serve_payload') as serve:
        home_report_http._page(None, None, 'fixture')
    page = serve.call_args.args[2].decode()
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'report_preview.browser.cjs')],
        input=json.dumps({'page': page}),
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_explanation_manifest_visual_preview():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'home_explanation.browser.cjs'), '--preview-only'],
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
