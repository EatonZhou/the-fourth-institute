"""Recover native identities from durable metadata; never alter logical sessions."""
import json
import hashlib
from datetime import datetime


def kimi_prompt_ids(path):
    """Return linkage metadata only, never retain input or response text."""
    result = set()
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if 'turn.prompt' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('type') == 'turn.prompt' and row.get('promptId'):
                result.add(str(row['promptId']))
    return sorted(result)


def claude_prompt_ids(path):
    result = set()
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not ('"uuid"' in line and '"user"' in line or '"tool_use"' in line):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('type') == 'user' and row.get('uuid'):
                result.add(str(row['uuid']))
            if isinstance(row, dict):
                content = (row.get('message') or {}).get('content', [])
                if isinstance(content, list):
                    result.update(str(b['id']) for b in content if isinstance(b, dict)
                                  and b.get('type') == 'tool_use' and b.get('id'))
    return sorted(result)


def codex_item_ids(path):
    result = set()
    answers = set()
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if '"response_item"' not in line and '"agent_message"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            payload = row.get('payload') or {}
            if row.get('type') == 'response_item':
                for field in ('id', 'call_id'):
                    if payload.get(field):
                        result.add(str(payload[field]))
            text = ''
            if row.get('type') == 'event_msg' and payload.get('type') == 'agent_message':
                text = payload.get('message') or ''
            elif row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'assistant':
                text = ''.join(b.get('text', '') for b in payload.get('content', [])
                               if isinstance(b, dict) and b.get('type') == 'output_text')
            if isinstance(text, str) and text.strip() and row.get('timestamp'):
                for value in (text, text.strip()):
                    answers.add((hashlib.sha256(value.encode()).hexdigest(), row['timestamp']))
    return {'ids': sorted(result), 'answers': sorted(answers)}


def recover_prompt_sessions(executions, sources, routes, cache=None):
    """Join exact native prompt IDs, rejecting cross-account or ambiguous matches."""
    from scripts.provider_usage_native import record_path
    pending = [r for r in executions if (r['provider'] == 'kimi' and r.get('provider_turn_id')
               or r['provider'] == 'claude' and r.get('native_prompt_id') or r.get('native_item_ids')
               or r.get('native_answer_hashes'))
               and not r.get('native_session_id')
               and not r.get('native_link_conflict')
               and (not r.get('backend_session_id') or r['backend_session_id'].startswith('execution-'))]
    if not pending:
        return
    def identities(record):
        values = set(record.get('native_item_ids', []))
        if record.get('native_prompt_id'):
            values.add(record['native_prompt_id'])
        if record['provider'] == 'kimi':
            values.add(str(record['provider_turn_id']))
        return values
    wanted = {(r['provider'], identity) for r in pending for identity in identities(r)}
    wanted_providers = {r['provider'] for r in pending}
    answer_hashes = {h for r in pending for h in r.get('native_answer_hashes', [])}
    answer_matches = {}
    matches = {}
    seen = set()
    for account, source in sources:
        provider = source['format']
        load = {'kimi': kimi_prompt_ids, 'claude': claude_prompt_ids, 'codex': codex_item_ids}.get(provider)
        if load is None or provider not in wanted_providers or source['path'] in seen:
            continue
        seen.add(source['path'])
        p = record_path(source['path'])
        try:
            metadata = cache.metadata(p, provider + '-link-ids-v4', load) if cache else load(p)
        except (OSError, UnicodeError):
            source['provenance_scan_failed'] = True
            continue
        ids = metadata['ids'] if isinstance(metadata, dict) else metadata
        if isinstance(metadata, dict):
            for digest, at in metadata['answers']:
                if digest in answer_hashes:
                    answer_matches.setdefault(digest, []).append((account, source['session_id'], at))
        for prompt in {i for name, i in wanted if name == provider}.intersection(ids):
            matches.setdefault((provider, prompt), set()).add((account, source['session_id']))
    for record in pending:
        provider = record['provider']
        owner = routes.get((provider, record.get('backend_route_id')))
        evidence = [{sid for account, sid in matches.get((provider, identity), ())
                     if account == owner or account == (provider, 'unresolved-account')}
                    for identity in identities(record)]
        found = [s for s in evidence if s]
        answer_candidates = set()
        for digest in record.get('native_answer_hashes', []):
            for account, sid, at in answer_matches.get(digest, []):
                try:
                    occurred = datetime.fromisoformat(at.replace('Z', '+00:00'))
                    inside = record['started_at'] <= occurred <= record['finished_at']
                except (ValueError, TypeError):
                    inside = False
                if inside and (account == owner or account == (provider, 'unresolved-account')):
                    answer_candidates.add(sid)
        if answer_candidates:
            found.append(answer_candidates)
        candidates = set.intersection(*found) if found else set()
        if len(candidates) == 1:
            record['native_session_id'] = candidates.pop()
            record['native_link_basis'] = ('exact_native_answer_hash_and_interval' if answer_candidates
                                          else 'exact_native_item_id' if record.get('native_item_ids') else 'exact_native_prompt_id')
        elif found:
            record['native_link_conflict'] = True


def recover_native_sessions(connection, executions):
    by_id = {r['execution_id']: r for r in executions}
    if not by_id:
        return
    durable = {}
    for row in connection.execute('''SELECT data_json::jsonb->>'execution_id' AS execution_id,
        data_json::jsonb->>'native_session_id' AS native_session_id,
        data_json::jsonb->>'provider' AS provider
        FROM agent_operations WHERE kind='subscription_native_link'
        AND data_json::jsonb->>'execution_id'=ANY(%s)''', (list(by_id),)):
        record = by_id[row['execution_id']]
        if row['provider'] == record['provider'] and row['native_session_id']:
            durable.setdefault(row['execution_id'], set()).add(row['native_session_id'])
    for execution_id, values in durable.items():
        if len(values) == 1:
            by_id[execution_id]['native_session_id'] = next(iter(values))
            by_id[execution_id]['native_link_basis'] = 'durable_subscription_native_link'
        else:
            by_id[execution_id]['native_link_conflict'] = True
    # Select only linkage metadata, not response/prompt/diff bodies.
    rows = connection.execute('''SELECT data_json::jsonb->>'execution_id' AS execution_id,
        data_json::jsonb #>> '{content,backend_session_id}' AS backend_session_id,
        data_json::jsonb #>> '{content,thread_id}' AS thread_id,
        data_json::jsonb #>> '{content,provider}' AS provider,
        data_json::jsonb #>> '{content,type}' AS type
        FROM agent_operations WHERE kind='artifact'
        AND data_json::jsonb->>'execution_id'=ANY(%s)
        AND (data_json::jsonb #>> '{content,backend_session_id}' IS NOT NULL
             OR data_json::jsonb #>> '{content,thread_id}' IS NOT NULL)''', (list(by_id),))
    candidates = {}
    for row in rows:
        record = by_id[row['execution_id']]
        if row['provider'] and row['provider'] != record['provider']:
            continue
        native = row['backend_session_id']
        if not native and row['type'] == 'native_turn':
            native = row['thread_id']
        if native and native != record['execution_id'] and not native.startswith('execution-'):
            candidates.setdefault(record['execution_id'], set()).add(native)
    for execution_id, values in candidates.items():
        record = by_id[execution_id]
        if execution_id in durable:
            continue
        logical = record.get('backend_session_id') or ''
        if len(values) == 1 and (not logical or logical.startswith('execution-') or logical in values):
            record['native_session_id'] = next(iter(values))
            record['native_link_basis'] = 'durable_native_artifact'
        else:
            record['native_link_conflict'] = True
    pending = [r['execution_id'] for r in executions if r['provider'] in ('claude', 'codex')
               and not r.get('native_session_id') and not r.get('native_link_conflict')
               and (r.get('backend_session_id') or '').startswith('execution-')]
    if not pending:
        return
    for row in connection.execute('''SELECT execution_id,
        metadata_json::jsonb #>> '{provider_observation,item_id}' AS item_id
        FROM segments WHERE execution_id=ANY(%s)
        AND metadata_json::jsonb ? 'provider_observation' ''', (pending,)):
        item = row['item_id'] or ''
        if item.startswith(('msg_', 'call_', 'toolu_')):
            by_id[row['execution_id']].setdefault('native_item_ids', []).append(item)
    codex_pending = [identity for identity in pending if by_id[identity]['provider'] == 'codex']
    if codex_pending:
        for row in connection.execute('''SELECT s.execution_id,s.content_sha256,p.body
            FROM segments s JOIN payloads p ON p.payload_ref=s.content_ref
            WHERE s.execution_id=ANY(%s) AND s.segment_type='model'
            AND s.metadata_json::jsonb->>'kind'='final_answer'
            AND s.content_type LIKE 'text/plain%%' AND p.byte_length>0''', (codex_pending,)):
            by_id[row['execution_id']].setdefault('native_answer_hashes', []).append(row['content_sha256'])
            # Final-answer bytes only; never prompts, and never retained/exported.
            text = bytes(row['body']).decode('utf-8')
            if text.strip() and text != text.strip():
                by_id[row['execution_id']]['native_answer_hashes'].append(hashlib.sha256(text.strip().encode()).hexdigest())


def runtime_config_dirs(projects, lineages=None):
    """Current and retained pre-migration Project layouts, under configured roots."""
    result = set()
    for pattern in ('*/agents/*/residents/*/runtime/backend/claude/config',
                    '*/knowledge_base/agents/*/residents/*/runtime/backend/claude/config'):
        result.update(str(p) for p in projects.glob(pattern) if p.is_dir())
    if lineages is not None:
        result.update(str(p) for p in lineages.glob('*/residents/*/runtime/backend/claude/config') if p.is_dir())
    return result
