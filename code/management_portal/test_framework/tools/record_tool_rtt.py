"""Append an original orchestrator measurement; never infer missing timestamps."""
import argparse
import json
import os
from pathlib import Path
import re
import uuid


def record(directory, tool, started_ms, finished_ms, status):
    root = Path(directory).resolve()
    context = json.loads((root / 'run-context.json').read_text())
    if Path(context['evidence_dir']).resolve() != root:
        raise ValueError('Evidence directory does not match this run')
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,100}', tool) or status not in {'returned', 'failed'}:
        raise ValueError('Use a static tool name and outcome; never arguments or error text')
    if type(started_ms) is not int or type(finished_ms) is not int or min(started_ms, finished_ms) <= 0:
        raise ValueError('Original epoch millisecond timestamps are required')
    row = {'call_id': str(uuid.uuid4()), 'tool': tool, 'started_at_ms': started_ms,
           'finished_at_ms': finished_ms, 'duration_ms': finished_ms - started_ms if finished_ms >= started_ms else None,
           'clock': 'orchestrator_wall_ms', 'clock_valid': finished_ms >= started_ms,
           'status': status, 'run_id': context['run_id'], 'case_id': context['case_id'],
           'persistence': 'separate_evidence_maintenance_call_excluded_from_duration'}
    output = root / 'harness-tool-rtt.jsonl'
    if output.is_symlink():
        raise ValueError('Evidence output must not be a symbolic link')
    fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        os.write(fd, (json.dumps(row, ensure_ascii=False) + '\n').encode())
    finally:
        os.close(fd)
    return row


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('tool')
    parser.add_argument('started_ms', type=int)
    parser.add_argument('finished_ms', type=int)
    parser.add_argument('status', choices=['returned', 'failed'])
    args = parser.parse_args()
    record(args.directory, args.tool, args.started_ms, args.finished_ms, args.status)
