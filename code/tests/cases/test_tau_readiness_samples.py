"""Fixed native UI examples for TAU prompt and helper maintenance.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
import os
from pathlib import Path
import subprocess
import sys


def test_readiness_wait_requires_the_complete_original_predicate():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_readiness_wait.cjs')],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_original_component_identity_and_native_watch_preflight():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_component_preflight.cjs')],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_prompt_readiness_examples():
    """原生 Chat 不提供 Session 头部时，从真实 Menu 取得完整复制值。"""
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_home_chat_navigation.cjs'),
         '--readiness-samples'],
        env={**os.environ, 'TFI_TEST_PYTHON': sys.executable},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_hatg_fit_waits_for_mount_and_target_restoration():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_home_chat_navigation.cjs'),
         '--camera-samples'],
        env={**os.environ, 'TFI_TEST_PYTHON': sys.executable},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_loaded_graph_fit_survives_late_workbench_catalogs():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_home_chat_navigation.cjs'),
         '--late-camera-samples'],
        env={**os.environ, 'TFI_TEST_PYTHON': sys.executable},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_graph_readiness_preserves_geometry_and_execution_errors():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_home_chat_navigation.cjs'),
         '--graph-readiness-samples'],
        env={**os.environ, 'TFI_TEST_PYTHON': sys.executable},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_create_lineage_opens_first_resident_form():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_home_chat_navigation.cjs'),
         '--lineage-creation-samples'],
        env={**os.environ, 'TFI_TEST_PYTHON': sys.executable},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
