"""逐行测试结果用真实字节位置恢复，不提前解码尚未完成的UTF-8。

:module: operations.test-framework
:design: decision_8273271b67b1
:risk: no-external-side-effects
"""
import json
from types import SimpleNamespace

import pytest

from management_portal.test_framework.local_runs import PytestGroup


class Observer(PytestGroup):
    def __init__(self):
        super().__init__({}, [])
        self.events = []

    def handle_event(self, event, ctx, group_dir):
        self.events.append(event)


def event_bytes(event, newline=b'\n'):
    return json.dumps(event, ensure_ascii=False).encode('utf-8') + newline


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'])
def test_consumer_resumes_at_physical_offsets_and_delivers_appends_once(tmp_path, newline):
    """LF与CRLF日志均以磁盘实际字节计位，中文内容和后续追加事件不丢不重。"""
    path = tmp_path / 'results.jsonl'
    first = {'event': 'report', 'text': '中文失败证据'}
    second = {'event': 'finish', 'nodeid': 'tests/test_a.py::test_中文'}
    path.write_bytes(event_bytes(first, newline))
    group, ctx = Observer(), SimpleNamespace(run_dir=tmp_path)
    position = group._consume(path, 0, ctx, tmp_path)
    assert position == path.stat().st_size
    assert group.events == [first]
    assert group._consume(path, position, ctx, tmp_path) == position
    assert group.events == [first]
    with path.open('ab') as stream:
        stream.write(event_bytes(second, newline))
    position = group._consume(path, position, ctx, tmp_path)
    assert position == path.stat().st_size
    assert group.events == [first, second]


@pytest.mark.parametrize('split', [1, 2])
def test_partial_multibyte_tail_waits_for_complete_line(tmp_path, split):
    """已完成事件先消费，下一事件的中文编码截断留待追加完成再消费。"""
    first = {'event': 'start', 'nodeid': 'test_original'}
    second = {'event': 'report', 'text': '中'}
    prefix = event_bytes(first)
    tail = event_bytes(second)
    cut = tail.index('中'.encode()) + split
    path = tmp_path / 'results.jsonl'
    path.write_bytes(prefix + tail[:cut])
    group, ctx = Observer(), SimpleNamespace(run_dir=tmp_path)
    position = group._consume(path, 0, ctx, tmp_path)
    assert position == len(prefix)
    assert group.events == [first]
    with path.open('ab') as stream:
        stream.write(tail[cut:])
    assert group._consume(path, position, ctx, tmp_path) == path.stat().st_size
    assert group.events == [first, second]


def test_malformed_and_nonobject_lines_keep_existing_skip_contract(tmp_path):
    path = tmp_path / 'results.jsonl'
    expected = {'event': 'finish'}
    path.write_bytes(b'{bad json}\r\n[]\r\n' + event_bytes(expected, b'\r\n') + b'{"unfinished":')
    group = Observer()
    position = group._consume(path, 0, None, tmp_path)
    assert position == len(path.read_bytes()) - len(b'{"unfinished":')
    assert group.events == [expected]


def test_missing_and_out_of_scope_results_do_not_relax_artifact_boundary(tmp_path):
    group = Observer()
    assert group._consume(tmp_path / 'missing', 17, None, tmp_path) == 17
    inside = tmp_path / 'group'
    inside.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(event_bytes({'event': 'finish'}))
    with pytest.raises(RuntimeError, match='越过'):
        group._consume(outside, 0, None, inside)
    link = inside / 'results'
    link.symlink_to(outside)
    with pytest.raises(RuntimeError, match='越过'):
        group._consume(link, 0, None, inside)
    assert not group.events
