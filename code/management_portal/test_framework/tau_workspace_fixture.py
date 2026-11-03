"""Register fixture directories before handing them to background workers."""
from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from management_portal.shared_state import atomic_replace_json
from . import tau_fixture_paths


def prepare_workspace(root: Path, directory: Path, resident_id: str, *, instruction):
    """Use the case Resident's authenticated ISA and original binding request.

    Its allocation scope records the Workspace before a WALL background job
    reuses the directory. The binding grants no Workspace mutation permission.
    """
    content = tau_fixture_paths.inspect_content(root)
    if content is None:
        raise ValueError('A framework content fixture receipt is required')
    directory = Path(directory)
    if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(content):
        raise ValueError('Workspace directory is outside this content fixture')
    if not directory.is_dir() or not re.fullmatch(r'resident_[0-9a-f]{12}', resident_id):
        raise ValueError('Expected a fixture directory and exact Resident')
    path = Path(root) / 'fixture-workspaces.json'
    if path.is_symlink():
        raise ValueError('Workspace request journal cannot be a symlink')
    state = json.loads(path.read_text()) if path.exists() else {}
    directory = str(directory.resolve())
    request = state.get(directory)
    arguments = {'resident_id': resident_id, 'path': directory}
    if request and request['arguments'] != arguments:
        raise ValueError('Resume the original Workspace binding identity')
    if request is None:
        request = {'arguments': arguments, 'key': 'tau-workspace:' + uuid4().hex}
        state[directory] = request
        atomic_replace_json(path, state)
    if 'receipt' not in request:
        result = instruction('BindWorkspace', arguments, key=request['key'])
        receipt = result['binding']
        if (receipt.get('path') != directory
                or not re.fullmatch(r'workspace_[0-9a-f]{12}', receipt.get('workspace_id', ''))):
            raise ValueError('Workspace binding receipt does not match this fixture')
        request['receipt'] = {**receipt, **{key: result[key] for key in
            ('workspace_document_pending', 'operation_id') if key in result}}
        atomic_replace_json(path, state)
    return request['receipt']
