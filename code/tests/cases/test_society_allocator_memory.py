"""Validate counter ownership/meaning without sampling live application objects."""
import json
import subprocess
import sys
import textwrap

from management_portal.society_service import allocator_memory


def test_parser_exports_only_recognized_numeric_counters():
    value = allocator_memory._parse_pymalloc('''
# arenas allocated total = 1,234
# arenas allocated current = 21
21 arenas * 1048576 bytes/arena = 22,020,096
# bytes in allocated blocks = 16,000,000
# bytes in available blocks = 2,000,000
4 unused pools * 16384 bytes = 65,536
unrecognized-secret-label = 123
# bytes lost to pool headers = private-data
''')
    assert value == {'available': True, 'arenas_allocated_total': 1234,
                     'arenas_current': 21, 'arena_capacity_bytes': 22020096,
                     'allocated_block_bytes': 16000000,
                     'available_block_bytes': 2000000, 'unused_pool_bytes': 65536}


def test_counter_failures_do_not_export_exception_values(monkeypatch):
    def fail():
        raise OSError('secret-value-must-not-escape')
    monkeypatch.setattr(allocator_memory, 'native_heap_summary', fail)
    result = allocator_memory.summary()
    assert result['native_heap'] == {'available': False, 'error_type': 'OSError'}
    assert 'secret-value' not in json.dumps(result)


def test_real_allocator_reports_live_small_blocks_and_released_capacity():
    script = textwrap.dedent('''
        import json, sys
        from management_portal.society_service import allocator_memory as owner
        before = owner.summary()
        blocks = [dict(index=i, value='private-sentinel') for i in range(24000)]
        held = owner.summary()
        del blocks
        after = owner.summary()
        if before['pymalloc']['available']:
            assert held['pymalloc']['allocated_block_bytes'] > before['pymalloc']['allocated_block_bytes'] + 1000000
            assert after['pymalloc']['allocated_block_bytes'] < held['pymalloc']['allocated_block_bytes'] - 1000000
        native = after['native_heap']
        if native.get('allocator') == 'glibc':
            assert native['arena_capacity_bytes'] == native['in_use_arena_bytes'] + native['free_arena_bytes']
        assert 'private-sentinel' not in json.dumps([before, held, after])
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not result.stderr  # Counter capture never redirects/writes stderr.
