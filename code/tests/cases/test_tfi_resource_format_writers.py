"""Canonical resource writers and byte-preserving rejection of old formats.

:design: decision_15aab18c0ab3
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json

import pytest
from PIL import Image

from tests.cases.test_domain_tombstone_and_restore import (
    projection, descriptor, live_lineage_projection, R, L, T1, T2,
)
from management_portal.resources import domain_projection_evidence as evidence


def _writer_registry_snapshot(registry):
    with registry.database.read() as db:
        rows = db.execute('SELECT collection,document_id,document_json::text AS payload,revision,xmin::text AS stamp '
                          'FROM registry_documents ORDER BY collection,document_id').fetchall()
    return [tuple(row[key] for key in ('collection','document_id','payload','revision','stamp')) for row in rows]


def _writer_seed_delete(projection, namespace):
    item = projection.descriptors.normalize(descriptor(R, deleted=True))
    item['schema'] = f'{namespace}.collaboration-descriptor.v2'
    source = {'schema': f'{namespace}.domain-tombstone-source.v2', 'resource_id': R,
              'deleted_at': item['deleted_at'], 'source_fingerprint': evidence.projection_fingerprint(item)}
    receipt = {'schema': f'{namespace}.domain-tombstone-evidence.v2', 'resource_id': R,
               'domain_identity': 'society-service', 'incoming_descriptor': item,
               'descriptor_version': evidence.projection_fingerprint(item)}
    with projection.registry.writer('resident', evidence.SOURCE_COLLECTION, evidence.EVIDENCE_COLLECTION) as writer:
        writer.put('resident', R, item)
        writer.put(evidence.SOURCE_COLLECTION, R, source)
        writer.put(evidence.EVIDENCE_COLLECTION, R, receipt)
    return item


def test_writer_fresh_delete_persists_tfi_and_original_content_digest(projection):
    item = descriptor(R, deleted=True)
    result = projection.service.sync_collaboration_resources([{'descriptor': item}], domain_identity='society-service')
    assert result['schema'] == 'tfi.collaboration-sync-result.v1'
    stored = projection.registry.get('resident', R)
    source = projection.registry.get(evidence.SOURCE_COLLECTION, R)
    receipt = projection.registry.get(evidence.EVIDENCE_COLLECTION, R)
    assert stored['schema'] == 'tfi.collaboration-descriptor.v2'
    assert source['schema'] == 'tfi.domain-tombstone-source.v2'
    assert receipt['schema'] == 'tfi.domain-tombstone-evidence.v2'
    assert source['source_fingerprint'] == evidence.projection_fingerprint(stored)
    assert receipt['descriptor_version'] == evidence.projection_fingerprint(stored)
    assert item['schema'] == 'tfi.collaboration-descriptor.v2'


@pytest.mark.parametrize('stored_namespace', ['archive','tfi'])
@pytest.mark.parametrize('incoming_namespace', ['archive','tfi'])
def test_writer_deletion_retry_keeps_original_receipts_and_xmin(projection, stored_namespace, incoming_namespace):
    item = _writer_seed_delete(projection, stored_namespace)
    original = _writer_registry_snapshot(projection.registry)
    incoming = {**item, 'schema': f'{incoming_namespace}.collaboration-descriptor.v2'}
    if stored_namespace == incoming_namespace == 'tfi':
        result = projection.service.sync_collaboration_resources([{'descriptor': incoming}], domain_identity='society-service')
        assert result['resources'][0]['descriptor']['title'] == item['title']
    else:
        with pytest.raises(ValueError, match='unsupported collaboration descriptor schema'):
            projection.service.sync_collaboration_resources([{'descriptor': incoming}], domain_identity='society-service')
    assert _writer_registry_snapshot(projection.registry) == original
    # A valid format must still exercise stale-content and identity checks.
    incoming = _writer_seed_delete(projection, 'tfi')
    original = _writer_registry_snapshot(projection.registry)
    for patch in ({'title':'forged'}, {'updated_at':T2}, {'schema':'tfi.collaboration-descriptor.v3'}, {'schema':4}):
        reason = 'unsupported collaboration descriptor schema' if 'schema' in patch else 'Stale domain tombstone'
        with pytest.raises(ValueError, match=reason):
            projection.service.sync_collaboration_resources([{'descriptor': {**incoming, **patch}}], domain_identity='society-service')
        assert _writer_registry_snapshot(projection.registry) == original
    with pytest.raises(PermissionError):
        projection.service.sync_collaboration_resources([{'descriptor':incoming}], domain_identity='forged-service')
    assert _writer_registry_snapshot(projection.registry) == original


@pytest.mark.parametrize('patch', [{'source_fingerprint':'0'*64}, {'schema':'tfi.domain-tombstone-source.v3'},
                                  {'resource_id':L}, {'extra':'not part of accepted source'}])
def test_writer_retry_cannot_bypass_original_source_digest(projection, patch):
    item = _writer_seed_delete(projection, 'tfi')
    with projection.registry.writer(evidence.SOURCE_COLLECTION) as writer:
        writer.put(evidence.SOURCE_COLLECTION, R, {**writer.get(evidence.SOURCE_COLLECTION,R), **patch})
    before = _writer_registry_snapshot(projection.registry)
    with pytest.raises(ValueError, match='Stale domain tombstone'):
        projection.service.sync_collaboration_resources([{'descriptor':{**item,'schema':'tfi.collaboration-descriptor.v2'}}])
    assert _writer_registry_snapshot(projection.registry) == before


@pytest.mark.parametrize('namespace', ['archive','tfi'])
def test_writer_live_retry_keeps_later_independent_edit_and_original_source(projection, namespace):
    live = live_lineage_projection(L)
    item = projection.descriptors.normalize(live['descriptor'])
    item['schema'] = f'{namespace}.collaboration-descriptor.v2'
    with projection.registry.writer('lineage','domain_live_sources') as writer:
        writer.put('lineage', L, {**item,'title':'independent edit','updated_at':T2})
        writer.put('domain_live_sources', L, item)
    before = _writer_registry_snapshot(projection.registry)
    for prefix in ('archive','tfi'):
        incoming = {**item,'schema':f'{prefix}.collaboration-descriptor.v2'}
        if prefix == 'archive':
            with pytest.raises(ValueError, match='unsupported collaboration descriptor schema'):
                projection.service.sync_collaboration_resources([{'descriptor':incoming}], domain_identity='society-service')
        elif namespace == 'archive':
            with pytest.raises(ValueError, match='unsupported collaboration descriptor schema'):
                projection.service.sync_collaboration_resources([{'descriptor':incoming}], domain_identity='society-service')
        else:
            result = projection.service.sync_collaboration_resources([{'descriptor':incoming}], domain_identity='society-service')
            assert result['resources'][0]['descriptor']['title'] == 'independent edit'
        assert _writer_registry_snapshot(projection.registry) == before
    item = {**item, 'schema': 'tfi.collaboration-descriptor.v2'}
    with projection.registry.writer('lineage','domain_live_sources') as writer:
        writer.put('lineage', L, {**item, 'title':'independent edit', 'updated_at':T2})
        writer.put('domain_live_sources', L, item)
    before = _writer_registry_snapshot(projection.registry)
    with pytest.raises(ValueError, match='Stale canonical'):
        projection.service.sync_collaboration_resources([{'descriptor':{**item,'title':'forged'}}])
    assert _writer_registry_snapshot(projection.registry) == before


@pytest.mark.parametrize('namespace', ['archive','tfi'])
def test_writer_avatar_new_record_and_legacy_image_hash_are_stable(tmp_path, namespace):
    from management_portal.avatar_store import ScopeAvatarStore
    store = ScopeAvatarStore(tmp_path)
    subject = 'lineage:' + L
    image = io.BytesIO(); Image.new('RGB',(16,16),'blue').save(image,format='PNG')
    avatar = store.update(subject, {'kind':'upload','data_base64':base64.b64encode(image.getvalue()).decode()})
    path = store._path(subject)
    assert json.loads(path.read_text())['schema'] == 'tfi.scope-avatar.v1'
    image_bytes = store.image(subject, avatar['version'])
    assert hashlib.sha256(image_bytes).hexdigest() == avatar['version']
    image_files = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file() and p != path}
    record = json.loads(path.read_text()); record['schema'] = f'{namespace}.scope-avatar.v1'
    path.write_text(json.dumps(record))
    original = path.read_bytes(),path.stat().st_mtime_ns
    if namespace == 'archive':
        with pytest.raises(ValueError, match='Invalid avatar record'):
            ScopeAvatarStore(tmp_path).read(subject)
        with pytest.raises(ValueError, match='Invalid avatar record'):
            store.image(subject,avatar['version'])
    else:
        assert ScopeAvatarStore(tmp_path).read(subject) == avatar
        contents = store.image(subject,avatar['version'])
        assert hashlib.sha256(contents).hexdigest() == avatar['version']
    assert (path.read_bytes(),path.stat().st_mtime_ns) == original
    assert {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file() and p != path} == image_files
    for bad in ('tfi.scope-avatar.v2','archive.unrelated.v1',None,{},7):
        path.write_text(json.dumps({**record,'schema':bad}))
        with pytest.raises(ValueError): ScopeAvatarStore(tmp_path).read(subject)


def test_writer_content_snapshot_keeps_historical_bytes_and_hash():
    from management_portal.resources import content_changes as changes
    store = changes.ContentChangeStore()
    old = {'schema':'archive.resource-content-change.v1','change_id':'historical','before':{'text':'old','sha256':'untouched'}}
    store.append('isolated','actor',old)
    def snapshot():
        with store.database.read() as db:
            row = db.execute("SELECT payload,xmin::text AS stamp FROM content_changes WHERE change_id='historical'").fetchone()
            return row['payload'],row['stamp']
    original = snapshot()
    capture = {'store':store,'actor':'actor','execution_id':'isolated','resource_id':R,'recorded':0,'errors':0}
    token = changes._CAPTURE.set(capture)
    try:
        changes.record_text_change(R,'/isolated/file','before','after',before_version={'number':1},after_version={'number':2})
    finally: changes._CAPTURE.reset(token)
    fresh = store.for_execution('isolated','actor')[-1]
    assert fresh['schema'] == 'tfi.resource-content-change.v1'
    assert fresh['before']['sha256'] == hashlib.sha256(b'before').hexdigest()
    assert fresh['after']['sha256'] == hashlib.sha256(b'after').hexdigest()
    assert store.for_changes(['historical'])[0]['change'] == old
    assert snapshot() == original and capture['errors'] == 0


def test_writer_identity_projection_keeps_encryption_and_digests():
    from management_portal.identity_store import decode, encode
    raw = {'two_factor_users':[{'username':'isolated','nonce':'exact nonce','ciphertext':'exact ciphertext',
                               'enabled_at':None,'last_counter':42}],
           'two_factor_recovery_codes':[{'username':'isolated','digest':'exact digest'}]}
    before = copy.deepcopy(raw)
    current = decode('two_factor',copy.deepcopy(raw))
    assert current['schema'] == 'tfi.portal-two-factor.v1'
    assert current['users']['isolated']['secret'] == {'nonce':'exact nonce','ciphertext':'exact ciphertext'}
    old = {**current,'schema':'archive.portal-two-factor.v1'}
    assert encode('two_factor',old) == encode('two_factor',current)
    assert raw == before
    for kind,tables,tag in [('access',{'user_accounts':[]},'portal-access'),
                            ('password_resets',{'password_reset_tokens':[]},'portal-password-resets'),
                            ('passkeys',{'passkey_users':[],'passkey_credentials':[]},'portal-passkeys')]:
        assert decode(kind,tables)['schema'] == f'tfi.{tag}.v1'


def test_writer_build_report_new_output_and_historical_json_no_rewrite():
    from management_portal import article_build_store as build
    build.save_report('article_0123456789ab',{'status':'ready','log':'original log','source_digest':'original digest'})
    fresh = build.report('article_0123456789ab')
    assert fresh['schema'] == 'tfi.article-build.v1'
    old = {'schema':'archive.article-build.v1','source_digest':'original digest'}
    with build.database().transaction() as db:
        db.execute('UPDATE build_reports SET report_json=%s::jsonb', (json.dumps(old),))
    def snapshot():
        with build.database().read() as db:
            row = db.execute('SELECT report_json::text AS payload,xmin::text AS stamp FROM build_reports').fetchone()
            return row['payload'],row['stamp']
    before = snapshot()
    assert build.report('article_0123456789ab')['schema'] == old['schema']
    assert snapshot() == before


def test_writer_resource_response_contracts():
    from management_portal.resources.changes import ChangeNotification, ResourceChange
    from management_portal.resources.portal import portal_route_contract
    from management_portal.resources.workspace_store import WorkspaceStore
    change = ChangeNotification(ResourceChange(resource_id=R), ('resident',), ('resident',)).as_dict()
    assert change['schema'] == 'tfi.resource_change_notification.v1'
    assert change['change']['resource_id'] == R
    assert portal_route_contract()['schema'] == 'tfi.resource-portal-routes.v1'
    row = {'workspace_id':'workspace_0123456789ab','title':'same','summary':'','path':'/same','deleted_at':None,'created_at':T1,'updated_at':T1}
    before = copy.deepcopy(row)
    result = WorkspaceStore.descriptor(row)
    assert result['schema'] == 'tfi.collaboration-descriptor.v2'
    assert result['attributes']['path'] == '/same' and row == before


@pytest.mark.parametrize('namespace', ['archive','tfi'])
def test_writer_legacy_source_v1_retry_keeps_original_authenticated_proof(projection, namespace):
    item = _writer_seed_delete(projection, 'tfi')
    with projection.registry.writer(evidence.SOURCE_COLLECTION) as writer:
        source = writer.get(evidence.SOURCE_COLLECTION,R)
        writer.put(evidence.SOURCE_COLLECTION,R,{**source,'schema':f'{namespace}.domain-tombstone-source.v1'})
    before = _writer_registry_snapshot(projection.registry)
    with pytest.raises(ValueError, match='Stale domain tombstone'):
        projection.service.sync_collaboration_resources([{'descriptor':item}], domain_identity='society-service')
    assert _writer_registry_snapshot(projection.registry) == before
    # Exercise receipt provenance separately with an otherwise valid v2 proof.
    proof = evidence.tombstone_source(item)
    receipt = projection.registry.get(evidence.EVIDENCE_COLLECTION, R)
    assert evidence.historical_tombstone_retry(proof, receipt, item)
    for patch in ({'domain_identity':''},{'incoming_descriptor':{**item,'title':'other'}}):
        assert not evidence.historical_tombstone_retry(proof, {**receipt, **patch}, item)
        with projection.registry.writer(evidence.EVIDENCE_COLLECTION) as writer:
            writer.put(evidence.EVIDENCE_COLLECTION,R,{**writer.get(evidence.EVIDENCE_COLLECTION,R),**patch})
        snapshot = _writer_registry_snapshot(projection.registry)
        with pytest.raises(ValueError,match='Stale domain tombstone'):
            projection.service.sync_collaboration_resources([{'descriptor':item}])
        assert _writer_registry_snapshot(projection.registry) == snapshot


def test_writer_registered_repo_article_device_and_ignores_keep_existing_rows(tmp_path):
    from tests.cases.test_resource_identity_backend import _make_repo
    from management_portal import resource_identity_backend as repos, article_identity_backend as articles
    from management_portal import device_identity_backend as devices, resource_auto_registration as auto
    from management_portal.resources.registry_store import get_registry_store
    checkout = tmp_path/'checkout'; _make_repo(checkout,'https://example.invalid/writer/repo.git')
    repo = repos.register_repo_checkout(checkout)
    article_root = tmp_path/'article'; article_root.mkdir()
    (article_root/'main.tex').write_text(r'\documentclass{article}\begin{document}Body\end{document}')
    article = articles.register_article(article_root)
    device = devices.register_device({'name':'isolated','device_kind':'workstation','hostname':'isolated-host'})
    registry = get_registry_store()
    for kind,record in [('repo',repo),('article',article),('device',device)]:
        assert registry.get(kind,record['id'])['schema'] == f'tfi.{kind}.v1'
    auto.suppress_paths('repo',repo['id'],[checkout])
    ignore_id = auto._ignore_id('repo',str(checkout))
    assert registry.get(auto.IGNORE_COLLECTION,ignore_id)['schema'] == 'tfi.resource-auto-ignores.v1'
    canonical_snapshot = _writer_registry_snapshot(registry)
    assert devices.resolve_device_address(device['id'])['schema'] == 'tfi.resource_resolution.v2'
    assert _writer_registry_snapshot(registry) == canonical_snapshot
    with registry.writer('repo','article','device',auto.IGNORE_COLLECTION) as writer:
        for kind,record in [('repo',repo),('article',article),('device',device)]:
            old = writer.get(kind,record['id']); old['schema'] = f'archive.{kind}.v1'
            writer.put(kind,record['id'],old)
        old_ignore = writer.get(auto.IGNORE_COLLECTION,ignore_id)
        writer.put(auto.IGNORE_COLLECTION,ignore_id,{**old_ignore,'schema':'archive.resource-auto-ignores.v1'})
    snapshot = _writer_registry_snapshot(registry)
    assert repos.resolve_repo_address(repo['id'])['schema'] == 'tfi.resource_resolution.v2'
    assert articles.resolve_article_address(article['id'])['schema'] == 'tfi.resource_resolution.v2'
    with pytest.raises(ValueError, match='invalid schema'):
        devices.resolve_device_address(device['id'])
    assert str(checkout) in auto.suppressed_paths('repo')
    assert _writer_registry_snapshot(registry) == snapshot


def test_writer_knowledge_graph_response_keeps_actor_boundary(monkeypatch):
    from unittest.mock import Mock
    from knowledge_board_lan import knowledge_backend as knowledge
    index = Mock(return_value={"items":[]})
    service = Mock()
    service.query.return_value = {"resources":[]}
    monkeypatch.setattr(knowledge, "resource_index_snapshot", index)
    monkeypatch.setattr(knowledge, "get_resource_service", lambda:service)
    result = knowledge.build_knowledge_graph(actor="isolated-writer", refresh=True)
    assert result["schema"] == "tfi.knowledge_graph.v1"
    assert result["nodes"] == result["edges"] == []
    assert index.call_args.kwargs["actor"] == "isolated-writer"
    assert service.query.call_args.kwargs["actor"] == "isolated-writer"
    with pytest.raises(ValueError, match="actor required"):
        knowledge.build_knowledge_graph(actor="",refresh=True)


"""Paper/Repo canonical contracts; temporary data and isolated service boundaries.

:design: decision_15aab18c0ab3
"""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from management_portal import paper_backend as prw_paper
from management_portal import paper_collector as prw_collector
from management_portal import paper_projection as prw_projection
from management_portal import repo_credentials_backend as prw_credentials
from management_portal import repo_workspace_backend as prw_workspace


def _prw_file_state(path):
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest(), path.stat().st_mtime_ns


def _prw_forbid_write(*args, **kwargs):
    raise AssertionError("reader must not write historical data")


def test_paper_writer_new_manifest_and_legacy_fingerprints(tmp_path, monkeypatch):
    store = prw_paper.PaperStore(tmp_path / "papers")
    paper = {"id": "paper_0123456789ab", "title": "Stable paper identity", "year": "2026",
             "doi": "10.0000/synthetic", "_identifiers": {"doi": "10.0000/synthetic"}}
    root = store.items_root / "2026" / "synthetic"
    manifest = store.write_manifest(paper, root)
    assert manifest["schema"] == "tfi.paper.v1"
    assert store.read_manifest(root / "manifest.json") == manifest
    legacy = {**manifest, "schema": "archive.paper.v1"}
    legacy_path = root / "legacy.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    before = _prw_file_state(legacy_path)
    old_fingerprints = store.fingerprints_for_payload(legacy)
    new_fingerprints = store.fingerprints_for_payload(manifest)
    assert old_fingerprints == new_fingerprints
    assert "doi:10.0000/synthetic" in old_fingerprints
    store.index_path("fingerprints.json").write_text(
        json.dumps({key: paper["id"] for key in old_fingerprints}), encoding="utf-8")
    monkeypatch.setattr(prw_paper, "_write_json", _prw_forbid_write)
    assert store.read_manifest(legacy_path) == legacy
    assert store.find_existing_id(legacy) == store.find_existing_id(manifest) == paper["id"]
    assert _prw_file_state(legacy_path) == before


@pytest.mark.parametrize("namespace", ["archive", "tfi"])
def test_paper_writer_receipt_resume_keeps_confirmed_identity(namespace, monkeypatch):
    from management_portal import resident_handoff

    old = {"schema": f"{namespace}.paper-resident-input.v1", "resident_id": "resident_0123456789ab",
           "lineage_id": "lineage_0123456789ab", "input_message_id": "msg_confirmed",
           "requested_message_id": "msg_requested", "reply_message_ids": ["msg_previous"]}
    before = copy.deepcopy(old)
    calls = []
    result = {"resident_id": old["resident_id"], "lineage_id": old["lineage_id"],
              "message_id": "msg_confirmed", "messages": [
                  {"role": "agent", "message_id": "msg_reply", "reply_to_message_id": "msg_confirmed"}]}

    def resume(**kwargs):
        calls.append(kwargs)
        return copy.deepcopy(result)

    monkeypatch.setattr(resident_handoff, "resume_and_wait", resume)
    if namespace == "archive":
        with pytest.raises(prw_projection.PaperInputIdentityError, match="requires schema"):
            prw_projection.resume(receipt=old, message_id="msg_confirmed")
        assert calls == [] and old == before
    canonical = {**old, "schema": "tfi.paper-resident-input.v1"}
    projected = prw_projection.resume(receipt=canonical, message_id="msg_confirmed")
    assert projected["invocation"]["schema"] == "tfi.paper-resident-input.v1"
    assert projected["input_message_id"] == "msg_confirmed"
    assert projected["reply_message_ids"] == ["msg_reply"]
    assert projected["invocation"]["requested_message_id"] == "msg_requested"
    assert calls == [{"message_id": "msg_confirmed"}]
    assert old == before
    with pytest.raises(prw_projection.PaperInputIdentityError, match="does not match"):
        prw_projection.resume(receipt=canonical, message_id="msg_other")
    for bad in [f"{namespace}.paper-resident-input.v2", None, {}, " tfi.paper-resident-input.v1"]:
        with pytest.raises(prw_projection.PaperInputIdentityError, match="requires schema"):
            prw_projection.input_receipt({**old, "schema": bad})
    with pytest.raises(prw_projection.PaperInputIdentityError, match="cannot resume"):
        prw_projection.resume(receipt={**canonical, "input_message_id": ""}, message_id="msg_confirmed")
    assert calls == [{"message_id": "msg_confirmed"}]


def test_paper_writer_collection_contract_prompt_and_result(monkeypatch):
    saved = []
    monkeypatch.setattr(prw_paper.paper_state_store, "append", lambda section, job: saved.append(copy.deepcopy(job)))
    monkeypatch.setattr(prw_paper, "_start_paper_collection_task", lambda job: job)
    with prw_collector.authenticated_request({"type": "user", "id": "writer-fixture"}):
        prw_paper.start_paper_collection({"direction": "existing library only", "max_papers": 1}, actor="writer-fixture")
    job = saved[0]
    assert job["collector_contract"] == "tfi.paper.collector.v2"
    assert job["created_by"] == "writer-fixture"
    assert job["requested_message_id"].startswith("msg_")
    prompt = prw_collector._prompt(job)
    assert '"schema":"tfi.paper.collector.result.v1"' in prompt
    assert "archive.paper.collector.result.v1" not in prompt
    assert prw_collector.RESULT_SCHEMA == "tfi.paper.collector.result.v1"
    for namespace in ("archive", "tfi"):
        raw = json.dumps({"schema": f"{namespace}.paper.collector.result.v1", "job_id": job["job_id"],
                          "status": "completed", "papers": [], "error": ""})
        parsed = json.loads(raw)
        if namespace == "archive":
            with pytest.raises(ValueError, match="schema/job identity"):
                prw_collector._validate_result(raw, job, set(), [])
        else:
            result, papers, registered = prw_collector._validate_result(raw, job, set(), [])
            assert result == parsed
            assert papers == [] and registered == 0
        for bad in (f"{namespace}.paper.collector.result.v2", None, {}):
            with pytest.raises(ValueError, match="schema/job identity"):
                prw_collector._validate_result(json.dumps({**parsed, "schema": bad}), job, set(), [])
    with pytest.raises(PermissionError, match="authenticated"):
        prw_paper.start_paper_collection({"direction": "must not launch"}, actor="writer-fixture")


@pytest.mark.parametrize("namespace", ["archive", "tfi"])
def test_paper_writer_figure_prompt_commit_and_content_digest(tmp_path, monkeypatch, namespace):
    paper = {"id": "paper_0123456789ab", "title": "Synthetic figure paper"}
    figure = {"figure_id": "fig-001"}
    image_path = tmp_path / "synthetic.png"
    image_path.write_bytes(b"fixture bytes; resident boundary is isolated")
    content = "解释正文 with original bytes"
    raw = json.dumps({"schema": f"{namespace}.paper.figure-explanation.result.v1",
                      "paper_id": paper["id"], "figure_id": figure["figure_id"],
                      "status": "generated", "explanation": content, "error": ""})
    prompts = []

    def generate(prompt, *args, **kwargs):
        prompts.append(prompt)
        return raw, {"fixture": True}

    monkeypatch.setattr(prw_paper, "_paper_store_file", lambda *args: None)
    monkeypatch.setattr(prw_paper, "_read_text_preview", lambda *args, **kwargs: "")
    monkeypatch.setattr(prw_paper, "_figure_image_path", lambda *args: image_path)
    monkeypatch.setattr(prw_paper, "_run_figure_resident", generate)
    monkeypatch.setattr(prw_paper, "_paper_by_id", lambda paper_id: paper)
    monkeypatch.setattr(prw_paper, "_load_figures", lambda value: [copy.deepcopy(figure)])
    monkeypatch.setattr(prw_paper, "_set_figure_fields", lambda paper_id, figure_id, updates: figure.update(updates))
    original_figure, original_image = copy.deepcopy(figure), _prw_file_state(image_path)
    if namespace == "archive":
        with pytest.raises(ValueError, match="schema or Paper/Figure identity mismatch"):
            prw_paper.explain_figure({"paper_id": paper["id"], "figure_id": figure["figure_id"]}, "writer-fixture")
        assert figure == original_figure
    else:
        prw_paper.explain_figure({"paper_id": paper["id"], "figure_id": figure["figure_id"]}, "writer-fixture")
    assert '"schema": "tfi.paper.figure-explanation.result.v1"' in prompts[0]
    assert _prw_file_state(image_path) == original_image
    if namespace == "tfi":
        commit = figure["explanation_commit"]
        assert commit["schema"] == "tfi.paper.figure-explanation.commit.v1"
        assert commit["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
        assert figure["explanation"] == content
        assert commit["paper_id"] == paper["id"] and commit["figure_id"] == figure["figure_id"]


def test_repo_writer_new_encrypted_record_and_history_read_only(tmp_path, monkeypatch):
    root, key = tmp_path / "credentials", tmp_path / "test.key"
    context = {"protocol": "https", "host": "fixture.invalid", "auth_type": "token"}
    metadata = prw_credentials.save_credential("writer-fixture", context, username="fixture",
                                              secret="synthetic-only", root=root, key_path=key)
    cipher = Fernet(key.read_bytes())
    record_path = next(root.rglob("*.credential"))
    record = json.loads(cipher.decrypt(record_path.read_bytes()))
    assert record["schema"] == "tfi.repo-git-credential.v1"
    assert record["credential_id"] == metadata["credential_id"]
    assert "secret" not in metadata
    for namespace in ("archive", "tfi"):
        original = {**record, "schema": f"{namespace}.repo-git-credential.v1"}
        plaintext = json.dumps(original, sort_keys=True).encode()
        record_path.write_bytes(cipher.encrypt(plaintext))
        record_path.chmod(0o600)
        before = _prw_file_state(record_path)
        with monkeypatch.context() as guard:
            guard.setattr(prw_credentials, "_write_record", _prw_forbid_write)
            guard.setattr(prw_credentials, "_atomic_write", _prw_forbid_write)
            if namespace == "archive":
                for read in (
                    lambda: prw_credentials.find_credential("writer-fixture", {**context, "username": "fixture"}, root=root, key_path=key),
                    lambda: prw_credentials.list_credentials("writer-fixture", root=root, key_path=key),
                ):
                    with pytest.raises(prw_credentials.RepoCredentialStoreError, match="unsupported schema") as error:
                        read()
                    assert original["secret"] not in str(error.value)
            else:
                found = prw_credentials.find_credential("writer-fixture", {**context, "username": "fixture"}, root=root, key_path=key)
                listed = prw_credentials.list_credentials("writer-fixture", root=root, key_path=key)
                assert found == original
                assert listed["schema"] == "tfi.repo-git-credentials.v1"
                assert listed["credentials"][0]["credential_id"] == metadata["credential_id"]
                assert "secret" not in listed["credentials"][0]
            assert _prw_file_state(record_path) == before
            assert cipher.decrypt(record_path.read_bytes()) == plaintext
            with pytest.raises(prw_credentials.RepoCredentialStoreError, match="cannot be decrypted"):
                prw_credentials._read_record(record_path, Fernet(Fernet.generate_key()))
            assert _prw_file_state(record_path) == before
        for bad in (f"{namespace}.repo-git-credential.v2", None, {}):
            record_path.write_bytes(cipher.encrypt(json.dumps({**original, "schema": bad}).encode()))
            bad_before = _prw_file_state(record_path)
            with pytest.raises(prw_credentials.RepoCredentialStoreError, match="unsupported schema"):
                prw_credentials._read_record(record_path, cipher)
            assert _prw_file_state(record_path) == bad_before
    # Saving must not silently convert an old record, even with the right key.
    record_path.write_bytes(cipher.encrypt(json.dumps({**record, "schema": "archive.repo-git-credential.v1"}).encode()))
    before = _prw_file_state(record_path)
    with pytest.raises(prw_credentials.RepoCredentialStoreError, match="unsupported schema"):
        prw_credentials.save_credential("writer-fixture", context, username="fixture",
                                       secret="replacement-fixture", root=root, key_path=key)
    assert _prw_file_state(record_path) == before
    record_path.write_bytes(cipher.encrypt(json.dumps(record).encode()))
    # An explicit canonical update keeps its credential identity and creation time.
    replacement = prw_credentials.save_credential("writer-fixture", context, username="fixture",
                                                 secret="replacement-fixture", root=root, key_path=key)
    assert replacement["credential_id"] == metadata["credential_id"]
    assert replacement["created_at"] == metadata["created_at"]
    assert json.loads(cipher.decrypt(record_path.read_bytes()))["schema"] == "tfi.repo-git-credential.v1"
    assert len(list(root.rglob("*.credential"))) == 1


def test_repo_writer_responses_and_clean_retry(tmp_path, monkeypatch):
    repo_id = "repo_0123456789ab"
    monkeypatch.setattr(prw_workspace, "_checkout", lambda value: ({"id": repo_id}, tmp_path))
    monkeypatch.setattr(prw_workspace, "_status_payload", lambda checkout: {"dirty": False, "conflict_count": 0})
    monkeypatch.setattr(prw_workspace, "_remote_records", lambda checkout: [])
    monkeypatch.setattr(prw_workspace, "_branch_name", lambda checkout: "main")
    monkeypatch.setattr(prw_workspace, "_automation_status_payload", lambda *args: {"id": repo_id, "dirty": False})
    monkeypatch.setattr(prw_workspace, "commit_repo_changes", _prw_forbid_write)
    monkeypatch.setattr(prw_workspace.resource_identity_backend, "load_repo_records", lambda: [])
    clean = prw_workspace.auto_commit_repo(repo_id, actor="writer-fixture")
    assert clean["schema"] == "tfi.repo-auto-commit.v1"
    assert not clean["committed"] and not clean["pushed"] and clean["outcome"] == "clean"
    assert prw_workspace.auto_commit_repo(repo_id, actor="writer-fixture") == clean
    assert prw_workspace.generate_repo_descriptions()["schema"] == "tfi.repo-descriptions.v1"
    settings = prw_workspace.list_repo_credentials(repo_id, actor="writer-fixture",
                                                  credential_root=tmp_path / "empty", credential_key_path=tmp_path / "missing.key")
    assert settings == {"schema": "tfi.repo-git-credential-settings.v1", "repo_id": repo_id, "remotes": [], "credentials": []}


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
