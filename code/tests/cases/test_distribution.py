"""Release invariants verified against isolated PostgreSQL and real files."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.distribution.store import ReleaseStore, database, digest
from management_portal.distribution.engineering import generate, inventory, tree_digest
from management_portal.distribution.sanitize import scrub, knowledge_links, prune_permissions


def test_selected_repo_and_paper_copy_actual_content(tmp_path):
    from management_portal.distribution.selection import resource_file_scope
    from management_portal.distribution.engineering import copy_content
    repo = tmp_path / 'checkout'
    repo.mkdir()
    (repo / 'program.py').write_text('print("ready")\n')
    (repo / '.git').mkdir()
    (repo / '.git/config').write_text('private remote')
    record = {'resource_type': 'repo', 'source': {'path': ''}, 'attributes': {'canonical_checkout': str(repo)}}
    item = resource_file_scope('repo_0123456789ab', record)
    target = tmp_path / 'export' / item['target']
    copy_content(item['source'], target, selected=set())
    assert (target / 'program.py').read_text() == 'print("ready")\n'
    assert not (target / '.git').exists()
    record['attributes']['canonical_checkout'] = str(tmp_path / 'missing')
    with pytest.raises(ValueError, match='canonical checkout'):
        resource_file_scope('repo_0123456789ab', record)
    paper = tmp_path / 'paper'
    paper.mkdir()
    (paper / 'manifest.json').write_text('{"id":"paper_0123456789ab"}')
    (paper / 'paper.pdf').write_bytes(b'%PDF-1.7\n\xff')
    item = resource_file_scope('paper_0123456789ab', {'resource_type': 'paper', 'source': {'path': str(paper / 'manifest.json')}})
    target = tmp_path / 'export' / item['target']
    copy_content(item['source'], target, selected=set(), knowledge=True)
    assert (target / 'paper.pdf').read_bytes() == (paper / 'paper.pdf').read_bytes()
    assert (target / 'manifest.json').is_file()


def test_upgrade_contract_requires_exact_inventory_and_dedicated_sql():
    from management_portal.distribution.engineering import validate_upgrade
    migration = {'id': 'schema-v2', 'path': 'code/migrations/v2.sql', 'sha256': 'a' * 64}
    declaration = {'from_manifest_sha256': ['b' * 64], 'migrations': [migration]}
    files = [{'path': migration['path'], 'sha256': migration['sha256']}]
    assert validate_upgrade(declaration, files) == declaration
    for invalid in [dict(declaration, from_manifest_sha256=['latest']), dict(declaration, migrations=[migration, migration]), dict(declaration, migrations=[dict(migration, path='database/distribution.sql')]), dict(declaration, migrations=[dict(migration, sha256='c' * 64)])]:
        with pytest.raises(ValueError):
            validate_upgrade(invalid, files)


def test_configs_snapshots_and_request_identity():
    store = ReleaseStore()
    config = store.save_config(name="release A", snapshot={"notes": "原始自然语言", "source": "Dev"}, actor="viewer")
    args = dict(actor="viewer", config_id=config["config_id"], request_key="once", mode="full_test", tau_validation=True)
    first = store.create_run(**args)
    store.save_config(name="release A changed", snapshot={"notes": "changed"}, actor="viewer", config_id=config["config_id"])
    assert store.create_run(**args)["run_id"] == first["run_id"]
    assert store.get_run(first["run_id"])["snapshot"]["notes"] == "原始自然语言"
    assert store.create_run(**{**args, "request_key": "again"})["run_id"] != first["run_id"]
    with pytest.raises(ValueError, match="different"):
        store.create_run(**{**args, "mode": "publish"})
    latest = store.save_config(name="current", snapshot={}, actor="viewer", config_id=config["config_id"])
    assert "revision" not in latest
    assert store.get_config(config["config_id"])["name"] == "current"
    assert first["supervisor_resident_id"] == "resident_e4f55ad12dae"
    assert first["tau_validation"] is True


def test_full_test_requires_supervision_and_behavior():
    store = ReleaseStore()
    run = store.create_run(actor="viewer", request_key="evidence", mode="full_test", snapshot={}, tau_validation=True)
    evidence = {"manifest": "manifest.json", "checks_passed": True}
    with pytest.raises(ValueError, match="supervisor"):
        store.report(run["run_id"], state="succeeded", stage="checks", evidence=evidence, expected_revision=1)
    evidence.update(supervisor_resident_id=run["supervisor_resident_id"], instance_url="http://localhost:9876", behavior_evidence=["selected repo read"])
    with pytest.raises(ValueError, match="TAU"):
        store.report(run["run_id"], state="succeeded", stage="checks", evidence=evidence, expected_revision=1)
    evidence["tau_status"] = "passed"
    assert store.report(run["run_id"], state="succeeded", stage="checks", evidence=evidence, expected_revision=1)["state"] == "succeeded"


def test_config_save_receipt_retries():
    store = ReleaseStore()
    args = dict(name='saved once', snapshot={'notes': 'exact text'}, actor='reader', request_key='create')
    first = store.save_config(**args)
    assert store.save_config(**args) == first
    update = dict(args, name='updated', config_id=first['config_id'], request_key='update')
    revised = store.save_config(**update)
    assert 'revision' not in revised
    assert revised['name'] == 'updated'
    assert store.save_config(**update) == revised
    with pytest.raises(ValueError):
        store.save_config(**{**update, 'name': 'different'})


def test_generation_failure_is_durable_outside_generation_lock(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from management_portal.distribution import service, database as source_db, selection
    store = ReleaseStore()
    run = store.create_run(actor='reader', request_key='failing-generation', mode='publish', snapshot={'source':'Dev','destination':str(tmp_path / 'output')})
    @contextmanager
    def broken_source():
        def dump(*args, **kwargs):
            raise ValueError('controlled export failure')
        yield SimpleNamespace(dump=dump, prepare_references=lambda *args: {})
    monkeypatch.setattr(source_db, 'read_snapshot', broken_source)
    monkeypatch.setattr(selection, 'resolve', lambda *args, **kwargs: {'unresolved':[], 'tables':{}, 'path_mappings':[]})
    monkeypatch.setattr(service, 'source_root', lambda _: tmp_path)
    with pytest.raises(ValueError, match='controlled export'):
        service.generate_run(run['run_id'])
    saved = store.get_run(run['run_id'])
    assert saved['state'] == 'failed' and saved['result']['error_type'] == 'ValueError'
    assert [stage['stage'] for stage in saved['stages']] == ['generation', 'generation']


def test_generation_attempt_receipt_retry_does_not_rewrite_artifact(tmp_path):
    from management_portal.distribution.service import generate_run
    from management_portal.distribution.engineering import checksum
    store = ReleaseStore()
    snapshot = {'source':str(tmp_path), 'destination':str(tmp_path / 'output')}
    run = store.create_run(actor='reader', request_key='attempt-receipt', mode='publish', snapshot=snapshot)
    package = tmp_path / 'output' / (run['run_id'] + '.attempt-2')
    package.mkdir(parents=True)
    manifest = package / 'manifest.json'
    manifest.write_text(json.dumps({'run_id':run['run_id'], 'selection_sha256':digest(run['snapshot']),'inventory':[]}))
    before = checksum(manifest)
    first = generate_run(run['run_id'], attempt=2)
    second = generate_run(run['run_id'], attempt=2)
    assert first['revision'] == second['revision'] and first['result']['manifest_sha256'] == before
    assert checksum(manifest) == before


def test_target_intent_and_installation_support_are_separate():
    from management_common.distribution_targets import resolve_targets, preflight_manifest
    snapshot = {'notes': 'legacy unchanged'}
    legacy = resolve_targets(snapshot, ['projects'])
    assert legacy['target_resolution'] == 'legacy-effective-linux' and 'target_platforms' not in snapshot
    saved = ReleaseStore().save_config(name='cross platform', actor='reader', snapshot={'target_platforms':['windows','linux','windows']})
    assert saved['snapshot']['target_platforms'] == ['windows', 'linux']
    resolved = resolve_targets(saved['snapshot'])
    assert resolved['installable'] and not resolved['capability_gaps']
    assert all(row['verification'] == 'not_run' for row in resolved['support_matrix'])
    assert preflight_manifest(resolved, host={'platform':'windows','architecture':'x86_64','os_version':'test'})['ok']
    assert not preflight_manifest(legacy, host={'platform':'macos','architecture':'aarch64','os_version':'test'})['ok']
    host = {'platform':'linux','architecture':'x86_64','os_version':'test'}
    assert preflight_manifest(legacy, host=host)['ok']
    assert not preflight_manifest({**legacy,'architecture_constraints':{'linux':['aarch64']}}, host=host)['ok']
    assert not preflight_manifest({**legacy,'dependency_requirements':[{'platform':'linux','names':['Python']}]}, host=host)['ok']
    unknown = ReleaseStore().save_config(name='future platform', actor='reader', snapshot={'target_platforms':['future-os','linux']})
    assert unknown['snapshot']['target_platforms'] == ['future-os','linux']
    unavailable = resolve_targets(unknown['snapshot'])
    assert not unavailable['installable']
    assert any(g['platform'] == 'future-os' and g['implementation'] == 'unsupported' for g in unavailable['capability_gaps'])
    with pytest.raises(ValueError):
        ReleaseStore().save_config(name='invalid',actor='reader',snapshot={'target_platforms':[]})


def test_history_control_states_cannot_replay():
    from management_portal.distribution.history import freeze
    source = {('lineage','resident_mailbox'): [{'resident_id':'r','message_id':'m'}],
              ('lineage','conversation_input_states'): [{'resident_id':'r','state':'queued'}],
              ('lineage','conversation_states'): [{'resident_id':'r','state_json':'{"working":true}'}],
              ('lineage','trigger_paths'): [{'status':'active', 'finished_at':None}],
              ('lineage','trigger_path_steps'): [{'status':'waiting', 'finished_at':None, 'checkpoint_json':{'resume':True}}],
              ('lineage','resident_sessions'): [{'resident_id':'r','session_json':'{"account_id":"source"}'}],
              ('lineage','lineage_nodes'): [{'node_id':'timer','lineage_id':'l','node_type':'event_source'}]}
    frozen, archive, evidence = freeze(source)
    assert source['lineage','conversation_input_states'][0]['state'] == 'queued'
    assert not frozen['lineage','resident_mailbox'] and not frozen['lineage','resident_sessions']
    assert frozen['lineage','conversation_input_states'][0]['state'] == 'cancelled'
    assert frozen['lineage','event_source_progress'][0]['checkpoint_json'] == {'paused': True}
    assert evidence['archived_control_rows'] == 6
    assert frozen['lineage','trigger_paths'][0]['status'] == 'cancelled'
    assert frozen['lineage','trigger_path_steps'][0]['finished_at'] is not None
    assert scrub(archive[0]['row'])['session_json'] == {}


def test_copy_anonymization_and_dangling_permissions():
    resident = "resident_123456789abc"
    value = {"sender_id": "human", "nested": {"sender_id": resident, "password": "private"}, "backend_role": "coding", "backend_route_id": "source-account"}
    cleaned = scrub(value)
    assert cleaned == {"sender_id": "tfi-exported-user", "nested": {"sender_id": resident}, "backend_role": "coding"}
    assert value["sender_id"] == "human"
    text = "---\nlinks:\n  related: [decision_111111111111, decision_222222222222]\n---\n[keep](decision_111111111111) [omit](decision_222222222222)"
    result = knowledge_links(text, {"decision_111111111111"})
    assert "decision_222222222222" not in result and "[keep](/r/decision_111111111111)" in result
    nodes = [{"scheme_id": "s", "tree": tree, "id": identity, "parent": "", "kind": "resident" if tree == "p" else ""}
             for tree, identity in (("p", resident), ("a", "@viewer"), ("r", "repo_111111111111"), ("r", "repo_222222222222"))]
    entries = [{"scheme_id": "s", "p_node": resident, "a_node": "@viewer", "r_node": "repo_" + suffix} for suffix in ("111111111111", "222222222222")]
    result = prune_permissions({"scheme_nodes": nodes, "scheme_entries": entries}, {resident, "repo_111111111111"})
    assert len(result["scheme_entries"]) == 1
    assert result["scheme_entries"][0]["r_node"] == "repo_111111111111"


def test_source_declarations_preserve_actor_and_credential_schema_fields(tmp_path):
    from management_portal.distribution.engineering import copy_content
    from management_portal.distribution.sanitize import ACCOUNT_FIELDS
    source = tmp_path / 'source'
    source.mkdir()
    declaration = {'type': 'user', 'title': {'en': 'User'},
                   'actor': {'kind': 'user', 'name': 'actor', 'title': {'en': 'Actor'}},
                   'properties': {key: {'type': 'string', 'title': {'en': key}}
                                  for key in [*sorted(ACCOUNT_FIELDS), 'password', 'secret', 'api_key', 'auth_json', 'session_token']},
                   'path': '/selected/source/schema.json', 'example': 'sk-' + 'a' * 32}
    original = json.dumps(declaration)
    (source / 'schema.json').write_text(original)
    (source / 'auth.json').write_text('{"secret":"must not be copied"}')
    target = tmp_path / 'copy'
    copy_content(source, target, selected=set(), mappings=[('/selected/source', '${code_root}')], anonymize=False)
    exported = json.loads((target / 'schema.json').read_text())
    assert exported['type'] == 'user' and exported['title'] == declaration['title']
    assert exported['actor'] == declaration['actor']
    assert exported['properties'] == declaration['properties']
    assert exported['path'] == '${code_root}/schema.json'
    assert exported['example'] == '[removed source credential]'
    assert not (target / 'auth.json').exists()
    assert (source / 'schema.json').read_text() == original
    # The same names in real data remain identity/authentication fields.
    history = {'sender': {'type': 'user', 'id': 'old-human', 'title': 'Old name'},
               'actor_ref': 'old-human', 'provider_account_id': 'old-account', 'password': 'private',
               'resident': {'actor_ref': 'resident_111111111111'}}
    (source / 'history.json').write_text(json.dumps(history))
    copy_content(source / 'history.json', target / 'history.json', selected=set())
    anonymized = json.loads((target / 'history.json').read_text())
    assert anonymized['sender']['id'] == anonymized['actor_ref'] == 'tfi-exported-user'
    assert anonymized['sender']['title'] == 'TFI portal'
    assert 'provider_account_id' not in anonymized and 'password' not in anonymized
    assert anonymized['resident']['actor_ref'] == 'resident_111111111111'


def test_engineering_real_content_no_history_or_auth(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    for filename in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "scripts/install_tfi_release", "scripts/tfi_release_install.py", "nested/manifest.json"):
        target = source / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}" if filename.endswith(".json") else "test content\n")
    registry = Path(__file__).resolve().parents[2] / 'config/resource-types'
    import shutil
    shutil.copytree(registry, source / 'config/resource-types')
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text("print('selected code')\n")
    (repo / ".git").mkdir()
    (repo / ".git/history").write_text("private history")
    (repo / ".env").write_text("PRIVATE=secret")
    target = tmp_path / "package"
    resolved = {"selected_ids": ["repo_111111111111"], "unresolved": [], "features": [],
                "lineage_options": {"lineage_111111111111": {"history": True, "workspace": {"own_outputs": False, "extra_bindings": "exclude"}}},
                "files": [{"source": str(repo), "target": "data/share_root/Codes/repo_111111111111"}],
                "generated_files": {"data/share_root/Lineages/a/workspace/.tfi-workspace": "owned"}}
    manifest = generate(source_root=source, destination=target, snapshot={"source": "Dev"}, resolved=resolved,
                        database_sql="-- selected database\n", run_id="run-local", supervisor_resident_id="resident_e4f55ad12dae")
    assert (target / "data/share_root/Codes/repo_111111111111/hello.py").read_text() == "print('selected code')\n"
    assert not list(target.rglob(".git")) and not list(target.rglob(".env"))
    assert any(item["path"] == "code/nested/manifest.json" for item in manifest["inventory"])
    from management_common.resource_identity import ResourceTypeRegistry
    exported = ResourceTypeRegistry(target / 'code/config/resource-types').declarations
    original = ResourceTypeRegistry(registry).declarations
    assert exported == original
    assert isinstance(exported['user']['title'], dict)
    assert manifest["inventory"] == inventory(target)
    assert manifest['lineage_options'] == resolved['lineage_options']
    assert manifest['runtime_dependencies']['profiles'] == ['core']
    assert json.loads((target / 'code/config/principal-labels.json').read_text()) == {
        'user': {'tfi-exported-user': 'TFI portal'}}
    assert set(manifest["files"]) >= {"share_root", "staging_root", "instances_root", "secrets_dir"}
    with pytest.raises(FileExistsError):
        generate(source_root=source, destination=target, snapshot={}, resolved=resolved, database_sql="")
    with pytest.raises(ValueError, match="isolated"):
        generate(source_root=source, destination=source / "output", snapshot={}, resolved=resolved, database_sql="")


def test_release_route_does_not_require_workbench_or_release_permissions(monkeypatch):
    from management_portal.workbench_projections import http
    monkeypatch.setattr(http, "get_service", lambda: pytest.fail("ordinary workbench permission path used"))
    result = http.post("/api/workbenches/release", {"id": "irrelevant", "panel": "packaging", "operation": "configs.save",
                      "arguments": {"name": "low privilege", "snapshot": {"notes": "keep me"}}}, "viewer")
    identity = result["config"]["config_id"]
    assert http.post("/api/workbenches/release", {"panel": "packaging", "operation": "configs.get", "arguments": {"config_id": identity}}, "viewer")["config"]["snapshot"]["notes"] == "keep me"


def test_packaging_preferences_are_separate_and_actor_scoped(monkeypatch):
    from management_portal.distribution import page
    from management_portal.workbench_projections import http
    monkeypatch.setattr(http, 'get_service', lambda: pytest.fail('ordinary permission path used'))
    def context(actor, app_id=''):
        if app_id not in {'', 'active-development'}:
            raise PermissionError('wrong workbench')
        return {'workbench_id': 'active-development', 'release_scope': 'packaging'}
    monkeypatch.setattr(page, 'context', context)
    assert http.get('/api/workbenches/native', {'name':['development'], 'panel':['packaging']}, 'reader')['workbench']['release_scope'] == 'packaging'
    value = {'pending': 'x' * 70_000}
    assert http.post('/api/workbenches/state', {'id':'active-development','panel':'packaging','value':value}, 'reader') == value
    assert http.get('/api/workbenches/state', {'id':['active-development'],'panel':['packaging']}, 'reader') == value
    assert http.get('/api/workbenches/state', {'id':['active-development'],'panel':['packaging']}, 'different-reader') == {}
    with pytest.raises(PermissionError):
        http.post('/api/workbenches/state', {'id':'other-app','panel':'packaging','value':{}}, 'reader')


def test_packaging_recovers_exact_old_draft_once(monkeypatch):
    from management_portal.distribution import page
    from management_portal.workbench_projections.store import Store
    monkeypatch.setattr(page, 'context', lambda actor, app_id='': {'workbench_id': 'published-development'})
    monkeypatch.setattr(Store, 'get', lambda *args: pytest.fail('ordinary workbench ACL used'))
    draft = {'submission': {'request_key': 'same-key', 'message_id': 'msg_old', 'text': '原任务\n不改正文'}, 'checklist': {'source': 'Release'}}
    original = json.dumps({'panels': {'packaging': draft, 'architecture': {'private': 'other panel'}}}, ensure_ascii=False)
    owner = Store()
    with owner.connect() as c:
        c.execute('INSERT INTO preferences VALUES(%s,%s,%s)', ('draft-reader', 'published-development', original))
        c.execute('INSERT INTO preferences VALUES(%s,%s,%s)', ('other-reader', 'published-development', json.dumps({'panels': {'packaging': {'other': True}}})))
    assert page.state('draft-reader', '') == draft
    assert page.state('other-reader', '') == {'other': True}
    assert page.state('no-draft-reader', '') == {}
    page.state('draft-reader', '', {})
    assert page.state('draft-reader', '') == {}  # Cleared new state cannot resurrect old submission.
    with owner.connect() as c:
        assert c.execute('SELECT data FROM preferences WHERE actor=%s AND app_id=%s', ('draft-reader', 'published-development')).fetchone()['data'] == original


def test_candidate_times_and_platform_catalog_are_json_safe(monkeypatch):
    from datetime import datetime, timezone
    from contextlib import contextmanager
    from management_portal.distribution import candidates, api, permissions
    timestamp = datetime(2026, 10, 19, 1, 2, 3, tzinfo=timezone.utc)
    def rows(schema, table, **kwargs):
        if table == 'schemes':
            return [{'scheme_id': 'scheme-1', 'document': {'title': 'selected', 'created_at': timestamp}}]
        return [{'created_at': timestamp, 'nested': [{'expires_at': timestamp}]}]
    @contextmanager
    def source():
        yield SimpleNamespace(read_index=lambda: {}, rows=rows)
    monkeypatch.setattr(candidates, 'read_snapshot', source)
    # Permission candidates now use the native owner instead of snapshot rows;
    # this case checks only the shared JSON time boundary. Native reads and
    # instance paging are covered by test_distribution_permission_candidates.
    monkeypatch.setattr(permissions, 'read_candidate', lambda *_: {'updated_at': timestamp})
    for operation, args in [('ListTemplates', {}), ('GetTemplate', {'template_id': 'template-1'}), ('ListPermissionSchemes', {}), ('GetPermissionScheme', {'scheme_id': 'scheme-1'}), ('ListPermissionEntries', {'scheme_id': 'scheme-1'})]:
        result = candidates.candidate(operation, args)
        encoded = json.dumps(result, ensure_ascii=False)
        assert '2026-10-19T01:02:03Z' in encoded or operation == 'ListPermissionSchemes'
    preferred = api.dispatch('targets', {}, 'reader')
    compatible = api.dispatch('candidates', {'operation': 'release_targets'}, 'reader')
    assert preferred == compatible
    assert all('gaps' in target for target in preferred['targets'])


def test_selected_sql_is_importable_and_source_is_unchanged(monkeypatch):
    import os
    import uuid
    import psycopg
    from psycopg import sql
    from management_portal.distribution.database import read_snapshot
    namespace = "export_" + uuid.uuid4().hex[:12]
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE", namespace)
    dsn = os.environ["PORTAL_DATABASE_URL"]
    schema = namespace + "_lineage"
    selected = "lineage_111111111111"
    omitted = "lineage_222222222222"
    resident = "resident_111111111111"
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        c.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        c.execute("CREATE TABLE lineages(lineage_id text PRIMARY KEY,title text,created_by text)")
        c.execute("CREATE TABLE lineage_nodes(node_id text PRIMARY KEY,lineage_id text REFERENCES lineages,node_type text)")
        c.execute("CREATE TABLE residents(resident_id text PRIMARY KEY,title text,created_by text)")
        c.execute("CREATE TABLE conversation_input_states(resident_id text REFERENCES residents,message_id text PRIMARY KEY,sender_id text)")
        c.execute("INSERT INTO lineages VALUES(%s,'chosen','source human'),(%s,'omitted','source human')", (selected, omitted))
        c.execute("INSERT INTO lineage_nodes VALUES(%s,%s,'resident')", (resident, selected))
        c.execute("INSERT INTO residents VALUES(%s,'agent','source human')", (resident,))
        c.execute("INSERT INTO conversation_input_states VALUES(%s,'message-1','source human')", (resident,))
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(namespace + '_agent')))
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(namespace + '_agent')))
        c.execute('CREATE TABLE chat_actor_activity(sequence_id text,actor text,latest_at timestamptz,PRIMARY KEY(sequence_id,actor))')
        c.execute("INSERT INTO chat_actor_activity VALUES('s','human-a','2026-01-02'),('s','human-b','2026-01-03'),('s',%s,'2026-01-01')", (resident,))
        c.execute('CREATE TABLE chat_message_positions(message_id text PRIMARY KEY,actor_ref text,recipient_users text[])')
        c.execute("INSERT INTO chat_message_positions VALUES('m1','human-a',ARRAY['human-b']),('m2',%s,ARRAY['human-a'])", (resident,))
        c.execute('CREATE TABLE payloads(payload_ref text PRIMARY KEY,body bytea,sha256 text,byte_length bigint)')
        c.execute("INSERT INTO payloads VALUES('p',%s,'old',1)", (json.dumps({'actor_ref':'human-a','recipient_users':['human-b'], 'kind':{'type':'structured'}}).encode(),))
        c.execute('CREATE TABLE provider_sessions(provider_session_ref text PRIMARY KEY)')
        c.execute('CREATE TABLE executions(execution_id text PRIMARY KEY,provider_session_ref text REFERENCES provider_sessions)')
        c.execute("INSERT INTO provider_sessions VALUES('source-session')")
        c.execute("INSERT INTO executions VALUES('historical-execution','source-session')")
    with read_snapshot() as source:
        definitions = source.definition_rows({selected})
        assert [row["lineage_id"] for row in definitions[("lineage", "lineages")]] == [selected]
        history = source.historical_rows({resident}, {selected})
        assert len(history[("lineage", "conversation_input_states")]) == 1
        selected_rows = {**definitions, **history, **{('agent', name): source.rows('agent', name)
                              for name in ['chat_actor_activity','chat_message_positions','payloads','executions']}}
        archive = []
        report = source.prepare_references(selected_rows, archive)
        assert not report['missing_foreign_keys'] and len(archive) == 1
        script = source.dump(selected_rows, frozen_history=archive)
    assert "source human" not in script and omitted not in script
    with psycopg.connect(dsn, autocommit=True) as c:
        # Unnamespaced schemas below exist only on this disposable pytest server.
        c.execute(script)
        assert c.execute("SELECT sender_id FROM lineage.conversation_input_states").fetchone()[0] == "tfi-exported-user"
        activity = c.execute('SELECT actor,latest_at::text FROM agent.chat_actor_activity ORDER BY actor').fetchall()
        assert len(activity) == 2
        actors = dict(activity)
        assert actors['tfi-exported-user'].startswith('2026-01-03')
        assert resident in actors
        assert c.execute('SELECT count(*) FROM agent.chat_message_positions').fetchone()[0] == 2
        assert c.execute("SELECT recipient_users FROM agent.chat_message_positions WHERE message_id='m1'").fetchone()[0] == ['tfi-exported-user']
        payload = json.loads(bytes(c.execute('SELECT body FROM agent.payloads').fetchone()[0]))
        assert payload['actor_ref'] == 'tfi-exported-user' and payload['recipient_users'] == ['tfi-exported-user']
        assert c.execute('SELECT provider_session_ref FROM agent.executions').fetchone()[0] is None
        assert c.execute('SELECT count(*) FROM agent.executions').fetchone()[0] == 1
        assert c.execute(sql.SQL("SELECT created_by FROM {}.lineages WHERE lineage_id=%s").format(sql.Identifier(schema)), (selected,)).fetchone()[0] == "source human"
        c.execute("DROP SCHEMA lineage CASCADE")
        c.execute('DROP SCHEMA agent CASCADE')
        c.execute('DROP SCHEMA distribution_history CASCADE')
        c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(namespace + '_agent')))
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_new_run_uses_current_configuration_and_old_receipt_keeps_snapshot():
    store = ReleaseStore()
    config = store.save_config(actor='cas-publisher', name='reviewed', snapshot={'notes': 'reviewed'})
    args = dict(actor='cas-publisher', request_key='reviewed-run', mode='publish',
                config_id=config['config_id'])
    run = store.create_run(**args)
    store.save_config(actor='cas-publisher', name='changed', snapshot={'notes': 'changed'},
                      config_id=config['config_id'])
    assert store.create_run(**args)['run_id'] == run['run_id']
    current = store.create_run(**{**args, 'request_key': 'new-run'})
    assert current['snapshot']['notes'] == 'changed'
    assert 'configuration_revision' not in current['provenance']
    assert store.get_run(run['run_id'])['snapshot']['notes'] == 'reviewed'
    assert len(store.list_runs(config['config_id'])) == 2
    # Already frozen old requests retain their original payload and retry contract.
    frozen = store.create_run(**{**args, 'request_key': 'legacy-frozen', 'snapshot': config['snapshot']})
    assert frozen['snapshot']['notes'] == 'reviewed'
    assert store.create_run(**{**args, 'request_key': 'legacy-frozen', 'snapshot': config['snapshot']})['run_id'] == frozen['run_id']


def test_existing_configuration_migrates_to_current_content_without_changing_runs():
    from management_portal.distribution.api import dispatch
    store = ReleaseStore()
    config = store.save_config(actor='current-content', name='v0.7', snapshot={'notes': 'saved'})
    run = store.create_run(actor='current-content', request_key='before-migration', mode='publish', config_id=config['config_id'])
    db = database()
    with db.transaction() as c:
        c.execute("ALTER TABLE configurations ADD COLUMN revision BIGINT NOT NULL DEFAULT 4")
    db._ready = False
    assert store.get_config(config['config_id']) == config
    assert 'revision' not in store.list_configs()[0]
    assert store.get_run(run['run_id'])['snapshot'] == run['snapshot']
    # A previously open browser's obsolete counter must not reject a current save.
    updated = dispatch('configs.save', {'name': 'v0.7', 'config_id': config['config_id'],
                       'snapshot': {'notes': 'latest'}, 'expected_revision': 1}, 'current-content')['config']
    assert updated['snapshot']['notes'] == 'latest'
    assert 'revision' not in updated
    assert len(store.list_configs()) == 1
    assert store.get_run(run['run_id'])['snapshot']['notes'] == 'saved'
