#!/usr/bin/env python3
"""Submit/read a pinned high-quality explanation review or record its verdict.

The shared HTTP domain is the review authority. This client uses only the current
Resident credential, never accepts an author/reviewer identity in the payload.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import resident_hatg_instruction as isa
from management_common.portal_agent_http import urlopen
from management_common import secure_paths


def request(resident_id, path, body=None, *, raw=False):
    actor = isa._resident_id(resident_id)
    result = isa.execute(actor, {'schema': isa.SCHEMA, 'instruction': 'GetResident',
        'arguments': {'resident_id': actor}}, request_timeout=30, max_attempts=1)
    resident = result.get('result', {}).get('resident', {})
    lineage = resident.get('lineage_id')
    if resident.get('resident_id') != actor or not re.fullmatch(r'lineage_[0-9a-f]{12}', str(lineage or '')):
        raise ValueError('无法核实当前Resident所属Lineage，未发起页面API请求')
    req = Request(isa.DEFAULT_PORTAL_URL + '/api/home-explanation/' + path,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={'Authorization': 'Bearer ' + isa._token(actor), 'X-Portal-Lineage-ID': lineage,
                           'Content-Type': 'application/json', 'Accept': 'application/json'})
    with urlopen(req, timeout=60) as response:
        if raw:
            return response.read()
        value = json.load(response)
        return value['authoring'] if path.startswith('authoring?') or path == 'media/authoring' else value['review']


def submit(address, sha256, task, resident_id, *, reviewer='', previous_review_id=None, call=None):
    return (call or request)(resident_id, 'reviews', {'address': address, 'sha256': sha256, 'task': task, 'reviewer': reviewer, 'previous_review_id': previous_review_id})


def prepare(kind, quality, task, resident_id, *, call=None):
    """Prepare this authenticated Resident's Family before writing an artifact."""
    return (call or request)(resident_id, 'reviews/prepare', {'kind': kind, 'quality': quality, 'task': task})


def accept_authoring(address, sha256, quality, authoring_id, resident_id):
    return request(resident_id, 'media/authoring', {'address': address, 'sha256': sha256,
        'quality': quality, 'authoring_id': authoring_id})


def export(review_id, output_dir, resident_id, *, group='subject', call=request):
    """Download only the pinned review members, never resolve their private origins."""
    record = call(resident_id, 'reviews?' + urlencode({'id': review_id}))
    manifest = (record or {}).get('handoff', {}).get(group)
    if not manifest:
        raise ValueError('本轮尚无固定交接清单；原blocked可由请求者显式--resume')
    from management_portal.quality_review_transfer import digest
    if digest(manifest['entries']) != manifest['digest']:
        raise ValueError('交接清单摘要不符')
    output_dir = Path(output_dir)
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    for entry in manifest['entries']:
        parts = secure_paths.relative_parts(entry['name'])
        data = call(resident_id, 'reviews/file?' + urlencode({'id': review_id,
            'sha256': entry['sha256'], 'group': group}), raw=True)
        if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('下载副本摘要或大小不符')
        with secure_paths.open_directory(output_dir, ('assets', *parts[:-1]), create=True) as directory:
            fd = secure_paths.open_child(directory, parts[-1], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
    result = {'review_id': review_id, 'group': group, 'manifest': manifest,
              'source_access': 'not_granted', 'files_root': 'assets'}
    with secure_paths.open_directory(output_dir) as directory:
        fd = secure_paths.open_child(directory, 'index.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resident-id', default='')
    parser.add_argument('--reviewer', default='')
    parser.add_argument('--address')
    parser.add_argument('--sha256')
    parser.add_argument('--task', default='')
    parser.add_argument('--prepare', choices=('page', 'video'), help='prepare Family reviewers before high creation')
    parser.add_argument('--quality', choices=('high',), help='required explicitly with --prepare')
    parser.add_argument('--previous-review-id')
    parser.add_argument('--read', metavar='REVIEW_ID')
    parser.add_argument('--read-authoring', metavar='AUTHORING_ID')
    parser.add_argument('--cancel', metavar='REVIEW_ID')
    parser.add_argument('--resume', metavar='REVIEW_ID')
    parser.add_argument('--export', metavar='REVIEW_ID')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--group', choices=('subject', 'evidence'), default='subject')
    parser.add_argument('--verdict-file', type=Path)
    args = parser.parse_args()
    try:
        if args.prepare:
            if args.quality != 'high' or not args.task.strip():
                parser.error('--prepare requires --quality high and --task')
            if any((args.export, args.resume, args.verdict_file, args.cancel, args.read,
                    args.read_authoring, args.address, args.sha256, args.previous_review_id, args.reviewer)):
                parser.error('--prepare cannot be combined with artifact or review operations')
            value = prepare(args.prepare, args.quality, args.task, args.resident_id)
        elif args.export:
            if not args.output_dir:
                parser.error('--export requires --output-dir (a new directory in your Workspace)')
            value = export(args.export, args.output_dir, args.resident_id, group=args.group)
        elif args.resume:
            value = request(args.resident_id, 'reviews/resume', {'review_id': args.resume, 'reviewer': args.reviewer})
        elif args.verdict_file:
            value = request(args.resident_id, 'reviews/verdict', json.loads(args.verdict_file.read_text()))
        elif args.cancel:
            value = request(args.resident_id, 'reviews/cancel', {'review_id': args.cancel})
        elif args.read:
            value = request(args.resident_id, 'reviews?' + urlencode({'id': args.read}))
        elif args.read_authoring:
            value = request(args.resident_id, 'authoring?' + urlencode({'id': args.read_authoring}))
        else:
            if not args.address or not args.sha256:
                parser.error('submit requires --address and --sha256')
            value = submit(args.address, args.sha256, args.task, args.resident_id, reviewer=args.reviewer, previous_review_id=args.previous_review_id)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        if value and value.get('status') == 'submission_failed':
            parser.exit(1, 'Review submission failed; fixed artifact remains saved.\n')
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, f'Review operation failed: {exc}\n')


if __name__ == '__main__':
    main()
