"""Long logical paths retain shared-state locking and creation semantics.

:module: operations.deployment
:design: decision_4aa13bc21e8e
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import json
import shutil
from management_common.filesystem_paths import filesystem_path
from management_portal import shared_state


def exercise_long_state(root: Path, module=shared_state):
    directory = root / ('a' * 85) / ('b' * 85) / ('c' * 85)
    target = directory / 'state.json'
    lock = directory / '.resource-locks' / ('d' * 64 + '.lock')
    try:
        assert len(str(lock)) > 260
        with module.exclusive_file_lock(lock):
            module.atomic_create_json(target, {'revision': 1})
            before = filesystem_path(target).read_bytes()
            try:
                module.atomic_create_json(target, {'revision': 99})
            except FileExistsError:
                pass
            else:
                raise AssertionError('Exclusive creation overwrote existing state')
            assert filesystem_path(target).read_bytes() == before
            module.atomic_replace_json(target, {'revision': 2, 'text': '中文'})
            module.atomic_replace_text(directory / 'text.txt', 'updated\n')
        with module.exclusive_file_lock(lock):
            assert json.loads(filesystem_path(target).read_text(encoding='utf-8')) == {'revision': 2, 'text': '中文'}
            assert filesystem_path(directory / 'text.txt').read_text() == 'updated\n'
        return {'lock_path_length': len(str(lock)), 'lock_reacquired': True, 'exclusive_create': True, 'json_replace': True, 'text_replace': True}
    finally:
        if filesystem_path(root).exists():
            shutil.rmtree(filesystem_path(root))


def test_long_shared_state(tmp_path):
    exercise_long_state(tmp_path / 'long-state')
