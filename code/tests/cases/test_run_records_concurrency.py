""":module: operations.test-workbench"""
import json
import subprocess
import sys

from management_portal.test_framework.catalog import ROOT
from management_portal.test_framework.records import RunRecords


def test_separate_processes_preserve_runs_and_shared_results(tmp_path):
    """多个执行进程保留各自运行及同一运行的结果

    并发创建和更新运行记录时，索引和结果均不丢失，文件权限保持私有。
    """
    path = tmp_path / 'runs.json'
    records = RunRecords(path)
    shared = records.create({'execution': {'groups': [{'group_id': 'pytest-1'}]}}, 'test-actor')['run_id']
    code = '''
import sys
from pathlib import Path
from management_portal.test_framework.records import RunRecords
records = RunRecords(Path(sys.argv[1]))
for i in range(8):
    marker = f'{sys.argv[3]}:{i}'
    created = records.create({}, marker)
    records.update(created['run_id'], {'status': 'passed'})
    records.append_result(sys.argv[2], 'pytest-1', {'marker': marker})
'''
    processes = [subprocess.Popen(
        [sys.executable, '-c', code, str(path), shared, str(worker)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for worker in range(4)]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stdout + stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    expected = {f'{worker}:{i}' for worker in range(4) for i in range(8)}
    recent = records.recent()
    assert len(recent) == 33
    assert {r['created_by'] for r in recent if r['run_id'] != shared} == expected
    record = records.get(shared)
    assert len(record['results']) == 32
    assert {r['marker'] for r in record['results']} == expected
    assert record['groups'][0]['results'] == record['results']
    index = json.loads(path.read_text())['runs']
    assert len(index) == 33
    assert all(r['status'] == 'passed' for r in index if r['run_id'] != shared)
    assert path.stat().st_mode & 0o777 == 0o600
