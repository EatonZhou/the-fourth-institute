#!/usr/bin/env python3
"""Submit a fixed explanation package to an independent Resident, idempotently.

Uses only the authenticated Resident ISA client. Receipt means submitted, never
passed. Existing manifests can be resubmitted after an interrupted build without
creating another review input. The reviewer reads sources with its own ACL.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import resident_hatg_instruction as isa

REVIEWER_TITLE = '解释页独立检查'


def submit(address: str, sha256: str, task: str, resident_id: str, *, reviewer: str = '', call=None) -> dict:
    if not re.fullmatch(r'workspace_[a-f0-9]{12}/file/[^\x00-\x1f?#\\]+', address):
        raise ValueError('Use the saved manifest’s formal Workspace address')
    if any(p in {'', '.', '..'} for p in address.split('/')) or not re.fullmatch(r'[a-f0-9]{64}', sha256):
        raise ValueError('Invalid fixed manifest reference')
    if not task.strip() or len(task) > 4000:
        raise ValueError('Provide the reader’s understanding task (1–4000 characters)')
    actor = isa._resident_id(resident_id)

    def invoke(instruction, arguments, key=None):
        body = {'schema': isa.SCHEMA, 'instruction': instruction, 'arguments': arguments,
                'idempotency_key': key or 'explanation-read:' + uuid.uuid4().hex}
        response = (call or isa.execute)(actor, body)
        if response.get('status') != 'committed':
            raise RuntimeError('Review instruction did not commit')
        return response['result']

    for name in ('ReadResource', 'SearchResidents', 'DeliverMessage'):
        invoke('GetInstructionSchema', {'instruction': name})
    read = invoke('ReadResource', {'address': address, 'max_bytes': 1024 * 1024})
    if read.get('truncated') or not ('text' in read or 'base64' in read):
        raise ValueError('Manifest cannot be read in full')
    data = read['text'].encode('utf-8') if 'text' in read else base64.b64decode(read['base64'], validate=True)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError('Saved manifest bytes do not match the requested version')
    if json.loads(data).get('schema') != 'portal-explanation/v1':
        raise ValueError('Unsupported explanation manifest')
    found = invoke('SearchResidents', {'resident_ids': [reviewer], 'can_send': True, 'limit': 1}
                   if reviewer else {'q': REVIEWER_TITLE, 'can_send': True, 'limit': 50})
    candidates = [r for r in found.get('residents', []) if r.get('resident_id') != actor
                  and (r.get('resident_id') == reviewer if reviewer else r.get('title') == REVIEWER_TITLE)]
    if len(candidates) != 1:
        raise ValueError('Need one accessible independent review Resident; specify its formal ID with --reviewer')
    reviewer = candidates[0]['resident_id']
    key = 'explanation-review:' + hashlib.sha256(f'{actor}\n{reviewer}\n{address}\n{sha256}'.encode()).hexdigest()
    request = {
        'schema': 'portal-explanation-review/v1', 'author_resident_id': actor,
        'manifest': {'address': address, 'sha256': sha256}, 'understanding_task': task.strip(),
        'guide': '/static/home_explanation_authoring.md',
        'requirements': '独立读取固定版本并真实操作，参照三篇案例的内容与体验质量；复杂度适合任务。'
        '不得代作者生成视频、改包或扩大权限。结果存自己的 Workspace，返回固定版本、结论、证据和限制。'
        'passed / changes_requested / blocked 区分；受理不代表通过。',
    }
    receipt = invoke('DeliverMessage', {'resident_id': reviewer, 'business_ref': address,
        'message': {'content_parts': [{'type': 'text', 'text': json.dumps(request, ensure_ascii=False, indent=2)}]}}, key)
    if not receipt.get('message_id'):
        raise RuntimeError('Review receipt lacks a message ID')
    return {'status': 'submitted', 'reviewer_resident_id': reviewer, 'message_id': receipt['message_id'],
            'manifest': request['manifest']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resident-id', default='')
    parser.add_argument('--reviewer', default='')
    parser.add_argument('--address', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--task', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(submit(args.address, args.sha256, args.task, args.resident_id,
                                reviewer=args.reviewer), ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, f'Independent review not submitted: {exc}\n')


if __name__ == '__main__':
    main()
