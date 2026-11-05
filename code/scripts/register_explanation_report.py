#!/usr/bin/env python3
"""Register a fixed explanation package as a formal report, without rebuilding it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import resident_hatg_instruction as isa
from management_common.portal_agent_http import urlopen


def register(address, sha256, resident_id, *, call=None):
    body = {'address': address, 'sha256': sha256}
    if call:
        result = call(resident_id, body)
    else:
        actor = isa._resident_id(resident_id)
        metadata = isa.execute(actor, {'schema': isa.SCHEMA, 'instruction': 'GetResident',
            'arguments': {'resident_id': actor}}, request_timeout=30, max_attempts=1)
        resident = metadata.get('result', {}).get('resident', {})
        lineage = resident.get('lineage_id')
        if resident.get('resident_id') != actor or not re.fullmatch(r'lineage_[0-9a-f]{12}', str(lineage or '')):
            raise ValueError('无法核实当前 Resident 所属 Lineage，未登记汇报')
        request = Request(isa.DEFAULT_PORTAL_URL + '/api/home-report/explanations',
            data=json.dumps(body).encode(), headers={
                'Authorization': 'Bearer ' + isa._token(actor), 'X-Portal-Lineage-ID': lineage,
                'Content-Type': 'application/json', 'Accept': 'application/json'})
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    reference = result.get('reference', {})
    target = reference.get('target', {})
    if (reference.get('view') != 'report' or target.get('kind') != 'report'
            or not re.fullmatch(r'report_[0-9a-f]{12}', str(target.get('id', '')))
            or type(target.get('revision')) is not int or target['revision'] < 1):
        raise ValueError('正式登记未返回有效 report_ID 与固定 revision')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resident-id', default='')
    parser.add_argument('--address', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    result = register(args.address, args.sha256, args.resident_id)
    print(json.dumps(result['reference'], ensure_ascii=False))


if __name__ == '__main__':
    main()
