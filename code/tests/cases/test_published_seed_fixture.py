"""发行测试读取固定来源，不修饰损坏的 inventory。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from tests.cases.fixtures.published_seed import published_seed


def test_committed_seed_ignores_worktree_but_keeps_invalid_inventory(tmp_path):
    seed = tmp_path / 'instance/example_instance/seed'
    seed.mkdir(parents=True)
    (seed / 'example.txt').write_text('committed example')
    invalid = {'inventory': [{'path': 'example.txt', 'sha256': 'invalid', 'size': 1}]}
    (seed / 'manifest.json').write_text(json.dumps(invalid))
    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init')
    git('add', 'instance')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'seed')
    (seed / 'example.txt').write_text('unpublished edit')
    (seed / 'untracked.txt').write_text('not part of this release')
    with patch.dict('os.environ', {'TFI_EXAMPLE_SEED': ''}), published_seed(tmp_path) as frozen:
        assert (frozen / 'example.txt').read_text() == 'committed example'
        assert not (frozen / 'untracked.txt').exists()
        assert json.loads((frozen / 'manifest.json').read_text()) == invalid


def test_explicit_candidate_is_used_without_repair(tmp_path):
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    (candidate / 'manifest.json').write_text('invalid json')
    with patch.dict('os.environ', {'TFI_EXAMPLE_SEED': str(candidate)}), published_seed(tmp_path) as seed:
        assert seed == candidate
        assert (seed / 'manifest.json').read_text() == 'invalid json'
