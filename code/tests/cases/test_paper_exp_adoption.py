"""Controlled adopt, proof validation and crash reconciliation; no model calls."""
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import paper_exp_adoption as adoption, paper_state_store as store


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def evidence(isolated_portal_case, monkeypatch):
    from management_portal import paper_store, paper_reading_jobs, permission_system, isa_client
    from management_portal.execution import agent_data_store
    from management_portal import paper_projection
    with isolated_portal_case() as root:
        paper = {"id": "paper_test", "_store_root": str(root)}
        target = root / "exp.md"
        job = {"job_id": "exp-original", "paper_id": paper["id"], "created_by": "original",
               "created_at": 1, "updated_at": 13, "status": "error", "step": "stale", "usage": {}}
        store.append("exp_jobs", job)
        attempt = "paper-exp:" + job["job_id"]
        context = {"application": "paper.exp", "attempt_ref": attempt, "subject_ref": paper["id"]}
        private_input = {"message_id": "input", "metadata": {"business_context": context}}
        text = "# Exp\n" + "\n".join("## " + name + "\nSynthetic evidence." for name in paper_reading_jobs.EXP_SECTIONS)
        usage = {"total_tokens": 123, "input_variant": {"business_ref": attempt, "role": "resident", "execution_id": "execution"}}
        reply = {"message_id": "reply", "role": "agent", "author_id": "resident", "reply_to_message_id": "input",
                 "content_parts": [{"type": "text", "text": text}], "usage": usage,
                 "generation_source": {"execution_id": "execution"}}
        state = {"message_id": "input", "state": "completed", "reply_message_ids": ["reply"],
                 "work": {"state": "completed", "execution_id": "execution", "primary_message_id": "input"}}
        public_input = {"message_id": "input", "role": "user", "author_id": "original",
                        "origin": {"initiated_by": {"kind": "user", "id": "original"}}}
        calls = []
        def isa(actor, instruction, args):
            calls.append(instruction)
            assert actor == "contributor"
            if instruction == "GetInputState": return {"input_state": state}
            assert instruction == "GetMessage"
            return {"message": reply if args["message_id"] == "reply" else public_input}
        monkeypatch.setattr(isa_client, "call", isa)
        monkeypatch.setattr(agent_data_store, "get_agent_data_store", lambda: SimpleNamespace(read_chat_messages=lambda resident: [private_input]))
        monkeypatch.setattr(permission_system, "permitted_ids", lambda *a: [paper["id"]])
        monkeypatch.setattr(paper_store, "DATA_ROOT", root)
        monkeypatch.setattr(paper_store, "_paper_by_id", lambda identity: paper)
        monkeypatch.setattr(paper_store, "_paper_store_file", lambda *a: target)
        monkeypatch.setattr(paper_store.PAPER_STORE, "update_paper", lambda *a: None)
        monkeypatch.setattr(paper_store.PAPER_STORE, "write_run_record", lambda *a: None)
        monkeypatch.setattr(paper_reading_jobs, "_notify_paper_projection", lambda *a, **kw: None)
        monkeypatch.setattr(paper_projection, "turn", lambda **kw: pytest.fail("adopt invoked model"))
        key = "task-" + digest(("paper:exp\0" + attempt).encode()); directory = root / "job_materials" / key
        directory.mkdir(parents=True)
        material = b"original immutable material"; material_hash = digest(material)
        manifest = {"schema": "tfi.paper-task-material.v1", "paper_id": paper["id"], "sha256": material_hash,
                    "metadata": {"actor": "original", "scope": "paper:exp", "attempt": attempt, "target_resident_id": "resident"}}
        encoded = json.dumps(manifest).encode(); (directory / "manifest.json").write_bytes(encoded)
        (directory / (material_hash + ".txt")).write_bytes(material)
        payload = {"paper_id": paper["id"], "job_id": job["job_id"], "resident_id": "resident", "input_message_id": "input",
                   "reply_message_id": "reply", "execution_id": "execution", "content_sha256": digest(text.encode()),
                   "manifest_sha256": digest(encoded), "expected_target_sha256": "", "dry_run": True}
        yield SimpleNamespace(payload=payload, target=target, reply=reply, state=state, input=private_input, public_input=public_input, calls=calls)


def test_adopt_dry_run_then_fenced_save_and_idempotency(evidence):
    e = evidence
    audit = adoption.adopt_exp(e.payload, "contributor")
    assert audit["unique_input"] and audit["verified"]["sections"] == 9
    assert not e.target.exists()
    payload = {**e.payload, "dry_run": False, "expected_job_updated_at": audit["expected_job_updated_at"]}
    result = adoption.adopt_exp(payload, "contributor")
    assert result["job"]["status"] == "done" and result["job"]["usage"]["total_tokens"] == 123
    original_bytes = e.target.read_bytes()
    assert adoption.adopt_exp(payload, "contributor")["idempotent"]
    assert e.target.read_bytes() == original_bytes
    assert store.get("exp_jobs", e.payload["job_id"])["status"] == "error"
    assert set(e.calls) == {"GetMessage", "GetInputState"}


@pytest.mark.parametrize("mutation", ["reply_to", "execution", "ambiguous", "incomplete", "target", "newer", "cas",
    "source_missing", "source_execution", "work_running", "work_missing", "primary_input",
    "other_reply", "input_author", "input_initiator", "resident_initiator"])
def test_adopt_rejects_incomplete_ambiguous_or_stale_proof(evidence, mutation):
    e = evidence; payload = deepcopy(e.payload)
    if mutation == "reply_to": e.reply["reply_to_message_id"] = "another"
    if mutation == "execution": e.state["work"]["execution_id"] = "another"
    if mutation == "source_missing": e.reply.pop("generation_source")
    if mutation == "source_execution": e.reply["generation_source"]["execution_id"] = "another"
    if mutation == "work_running": e.state["work"]["state"] = "running"
    if mutation == "work_missing": e.state.pop("work")
    if mutation == "primary_input": e.state["work"]["primary_message_id"] = "another"
    if mutation == "other_reply": e.state["reply_message_ids"] = ["another"]
    if mutation == "input_author": e.public_input["author_id"] = "another"
    if mutation == "input_initiator": e.public_input["origin"]["initiated_by"]["id"] = "another"
    if mutation == "resident_initiator": e.public_input["origin"]["initiated_by"]["kind"] = "resident"
    if mutation == "ambiguous": e.input["metadata"]["business_context"]["attempt_ref"] = "unrelated"
    if mutation == "incomplete":
        e.reply["content_parts"][0]["text"] = "# Exp\n## Metrics\nOnly one section"
        payload["content_sha256"] = digest(e.reply["content_parts"][0]["text"].encode())
    if mutation == "target": e.target.write_text("new artifact")
    if mutation == "newer": store.append("exp_jobs", {"job_id": "new", "paper_id": payload["paper_id"], "status": "running", "created_at": 2})
    if mutation == "cas": payload.update(dry_run=False, expected_job_updated_at=999)
    with pytest.raises(ValueError): adoption.adopt_exp(payload, "contributor")
    assert not e.target.exists() or e.target.read_text() == "new artifact"


def test_adopt_reconciles_file_saved_before_database_failure(evidence, monkeypatch):
    from management_portal import paper_reading_jobs
    e = evidence; audit = adoption.adopt_exp(e.payload, "contributor")
    payload = {**e.payload, "dry_run": False, "expected_job_updated_at": audit["expected_job_updated_at"]}
    original_set = paper_reading_jobs._set_exp_job
    with monkeypatch.context() as patch:
        patch.setattr(paper_reading_jobs, "_set_exp_job", lambda *a: (_ for _ in ()).throw(RuntimeError("DB interrupted")))
        with pytest.raises(RuntimeError, match="DB interrupted"): adoption.adopt_exp(payload, "contributor")
    assert e.target.exists()
    result = adoption.adopt_exp(payload, "contributor")
    assert result["reconcile_existing_save"] and result["job"]["status"] == "done"


def test_adopt_section_first_reply_without_title(evidence):
    e = evidence
    content = e.reply['content_parts'][0]['text'].split('\n', 1)[1]
    assert content.startswith('## ')
    e.reply['content_parts'][0]['text'] = content
    payload = {**e.payload, 'content_sha256': digest(content.encode())}
    audit = adoption.adopt_exp(payload, 'contributor')
    assert audit['unique_input'] and audit['verified']['sections'] == 9
    result = adoption.adopt_exp({**payload, 'dry_run': False,
        'expected_job_updated_at': audit['expected_job_updated_at']}, 'contributor')
    assert result['job']['status'] == 'done'
    assert e.target.read_text().split('---', 2)[-1].lstrip().startswith('## ')
