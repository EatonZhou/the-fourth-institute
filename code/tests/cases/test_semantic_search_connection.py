"""Semantic search reports operation failures independently of realtime transport."""
from pathlib import Path
import subprocess


def test_semantic_search_connection():
    source = Path(__file__).with_name("javascript") / "semantic_search_connection.cjs"
    result = subprocess.run(["node", "--test", str(source)], text=True,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_semantic_search_destroy_barrier():
    source = Path(__file__).with_name('javascript') / 'semantic_search_connection.cjs'
    result = subprocess.run(['node', '--test', '--test-name-pattern=destroy barrier', str(source)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
