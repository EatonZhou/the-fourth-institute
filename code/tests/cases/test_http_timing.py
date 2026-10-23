from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import mock

import pytest

from management_portal import http_timing


def test_stage_timings_accumulate_failures_without_retaining_context():
    values = {}
    with mock.patch.object(http_timing, 'perf_counter', side_effect=[1, 1.25, 2, 2.5]):
        with http_timing.capture_timings(values):
            assert http_timing.timed_call('portal_conv_entries', lambda x: x, 42) == 42
            with pytest.raises(KeyError):
                http_timing.timed_call('portal_conv_entries', {}.__getitem__, 'private-target')
    assert values == {'portal_conv_entries': 750}
    with mock.patch.object(http_timing, 'perf_counter', side_effect=AssertionError('context leaked')):
        assert http_timing.timed_call('portal_conv_entries', lambda: 'outside') == 'outside'
    assert 'private-target' not in str(values)


def test_timing_contexts_do_not_cross_concurrent_requests():
    barrier = Barrier(2)

    def request(stage):
        values = {}
        with http_timing.capture_timings(values):
            http_timing.timed_call(stage, barrier.wait, timeout=5)
        return values

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(request, 'portal_conv_identity')
        second = executor.submit(request, 'portal_conv_summary')
        assert set(first.result()) == {'portal_conv_identity'}
        assert set(second.result()) == {'portal_conv_summary'}


def test_timing_names_are_fixed_and_callback_is_not_run_for_unknown_names():
    callback = mock.Mock()
    with http_timing.capture_timings({}), pytest.raises(ValueError, match='Unknown HTTP timing stage'):
        http_timing.timed_call('resident_private', callback)
    callback.assert_not_called()
