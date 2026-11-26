"""Check or partially restore an Article through the fixed operations RunScript."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from management_common.principal import is_user_or_resident
from management_portal.resources import article_restore_commit, article_restore_preview
from scripts.portal_operations import script_context


def _output(result):
    from portal_script import set_output

    # Match the SDK's UTF-8 serialization, including JSON punctuation and
    # non-ASCII bytes. A large preview remains a complete existing artifact.
    data = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(data) > article_restore_preview.OUTPUT_BUDGET:
        raise ValueError('Preview output exceeds the bounded response budget')
    if len(data) <= 65536:
        set_output(result)
        return
    if result.get('mode') != 'dry_run':
        raise ValueError('Only a complete check report can use an output artifact')
    name = 'article-restore-check.json'
    path = Path(os.environ['PORTAL_SCRIPT_ARTIFACTS']) / name
    with path.open('xb') as stream:
        stream.write(data)
    set_output({'mode': result['mode'], 'artifact': name, 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest()})


def main() -> int:
    from management_common.portal_database import portal_database
    from management_portal.hatg.store import ExecutionStore
    from management_portal.resources import get_resource_service

    # The operations owner verifies the run and returns its authenticated
    # caller. Neither an argument nor a host service identity is an actor.
    run = script_context('article_restore')
    actor = run['caller_id']
    if not is_user_or_resident(actor):
        raise PermissionError('Article restoration requires the authenticated User or Resident')
    run_id = run['script_run_id']
    if not isinstance(run_id, str) or not re.fullmatch(r'script_run_[0-9a-f]{12,64}', run_id):
        raise ValueError('Article restoration requires the stable ScriptRun identity')
    arguments = dict(run['arguments'])
    step = arguments.pop('step', None)
    expected = article_restore_preview.REQUIRED
    if step == 'apply':
        expected |= article_restore_commit.EXTRA
    elif step != 'check':
        raise ValueError('Article restoration step must be check or apply')
    if set(arguments) != expected:
        raise ValueError('Article restoration accepts only its exact evidence and decision parameters')
    # A failed ScriptRun is immutable. A retry uses a new run, but the same
    # caller and exact evidence must resume the same owned creation intent.
    operation_id = 'article-restore-script:' + article_restore_preview.digest(
        {'caller': actor, 'arguments': arguments})

    service = get_resource_service()
    # This is a host ledger observation, not a new HATG execution or a grant.
    # Missing retained tables remain explicit missing evidence in preview.
    receipts = ExecutionStore(portal_database(schema='resource_instructions'), create=False)
    if step == 'check':
        result = article_restore_preview.preview(arguments=arguments, actor=actor,
                                                service=service, receipt_store=receipts)
    else:
        result = article_restore_commit.restore(arguments=arguments, actor=actor,
            service=service, receipt_store=receipts, operation_id=operation_id)
    _output(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
