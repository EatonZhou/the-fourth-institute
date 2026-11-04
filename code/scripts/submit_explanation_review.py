#!/usr/bin/env python3
"""Submit/read a pinned high-quality explanation review or record its verdict.

The shared HTTP domain is the review authority. This client uses only the current
Resident credential, never accepts an author/reviewer identity in the payload.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import resident_hatg_instruction as isa
from management_common.portal_agent_http import urlopen


def request(resident_id, path, body=None):
    actor = isa._resident_id(resident_id)
    req = Request(isa.DEFAULT_PORTAL_URL + '/api/home-explanation/' + path,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={'Authorization': 'Bearer ' + isa._token(actor), 'Content-Type': 'application/json', 'Accept': 'application/json'})
    with urlopen(req, timeout=60) as response:
        return json.load(response)['review']


def submit(address, sha256, task, resident_id, *, reviewer='', previous_review_id=None, call=None):
    return (call or request)(resident_id, 'reviews', {'address': address, 'sha256': sha256, 'task': task, 'reviewer': reviewer, 'previous_review_id': previous_review_id})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resident-id', default='')
    parser.add_argument('--reviewer', default='')
    parser.add_argument('--address')
    parser.add_argument('--sha256')
    parser.add_argument('--task', default='')
    parser.add_argument('--previous-review-id')
    parser.add_argument('--read', metavar='REVIEW_ID')
    parser.add_argument('--cancel', metavar='REVIEW_ID')
    parser.add_argument('--verdict-file', type=Path)
    args = parser.parse_args()
    try:
        if args.verdict_file:
            value = request(args.resident_id, 'reviews/verdict', json.loads(args.verdict_file.read_text()))
        elif args.cancel:
            value = request(args.resident_id, 'reviews/cancel', {'review_id': args.cancel})
        elif args.read:
            value = request(args.resident_id, 'reviews?' + urlencode({'id': args.read}))
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
