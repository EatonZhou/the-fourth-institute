"""Canonical Paper/Repo readers reject retired tags and retain authentication and encryption.

:design: decision_15aab18c0ab3
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from management_portal import paper_collection_jobs, paper_collector, paper_figure_jobs, paper_projection, paper_reading_jobs, paper_store, paper_summaries
from management_portal import repo_credentials_backend as credentials


PAPER_ID = "paper_0123456789ab"
FIGURE_ID = "fig-001"


def bad_tags(suffix, version):
    return [None, {}, [], 7, True, f"tfi.{suffix}.v999", f"archive.{suffix}.v999", f"archive.{suffix}.v{version}",
            f"tfi.unrelated.v{version}", f"vendor.{suffix}.v{version}",
            f" tfi.{suffix}.v{version}", f"archive.{suffix}.v{version} "]


def file_state(path):
    content = path.read_bytes()
    return content, hashlib.sha256(content).hexdigest(), path.stat().st_mtime_ns


def forbid_write(*args, **kwargs):
    raise AssertionError("format read must not write back")


@pytest.fixture
def figure_reader(tmp_path, monkeypatch):
    analysis = tmp_path / "analysis.md"
    analysis.write_text("# Original analysis\n")
    image = tmp_path / "figure.png"
    image.write_bytes(b"isolated image path; no model invocation")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"isolated PDF path; no model invocation")
    paper = {"id": PAPER_ID, "title": "Existing paper", "pdf_path": str(pdf)}
    figure = {"figure_id": FIGURE_ID, "caption": "Existing caption"}
    monkeypatch.setattr(paper_figure_jobs, "_paper_store_file", lambda *args: analysis)
    monkeypatch.setattr(paper_figure_jobs, "_safe_file_path", lambda value: pdf)
    monkeypatch.setattr(paper_figure_jobs, "_figure_image_path", lambda *args: image)
    monkeypatch.setattr(paper_store, "atomic_replace_json", forbid_write)
    monkeypatch.setattr(paper_summaries, "atomic_replace_json", paper_store.atomic_replace_json)
    monkeypatch.setattr(paper_figure_jobs, "atomic_replace_json", paper_store.atomic_replace_json)
    monkeypatch.setattr(paper_reading_jobs, "atomic_replace_json", paper_store.atomic_replace_json)
    usage = {"source": "isolated response fixture"}

    def read(payload):
        before = {p: file_state(p) for p in (analysis, image, pdf)}
        inputs = copy.deepcopy((paper, figure, payload))
        raw = json.dumps(payload, ensure_ascii=False)
        monkeypatch.setattr(paper_figure_jobs, "_run_figure_resident", lambda *a, **k: (raw, usage))
        try:
            return paper_figure_jobs._generate_figure_explanation(paper, figure)
        finally:
            assert (paper, figure, payload) == inputs
            assert {p: file_state(p) for p in before} == before
    return read, usage


def figure_result(tag):
    return {"schema": tag, "paper_id": PAPER_ID, "figure_id": FIGURE_ID,
            "status": "generated", "explanation": "  ## Original explanation\n", "error": ""}


@pytest.mark.parametrize("namespace", ["tfi"])
def test_figure_result_alias_keeps_content_usage_and_files(figure_reader, namespace):
    read, usage = figure_reader
    assert read(figure_result(f"{namespace}.paper.figure-explanation.result.v1")) == (
        "## Original explanation", usage)


def test_figure_reader_rejects_bad_tag_and_keeps_identity_and_status_checks(figure_reader):
    read, _ = figure_reader
    for tag in bad_tags("paper.figure-explanation.result", 1):
        with pytest.raises(ValueError, match="schema or Paper/Figure identity mismatch"):
            read(figure_result(tag))
    valid = figure_result("tfi.paper.figure-explanation.result.v1")
    for patch in ({"paper_id": "paper_ffffffffffff"}, {"figure_id": "fig-002"}):
        with pytest.raises(ValueError, match="identity mismatch"):
            read({**valid, **patch})
    with pytest.raises(RuntimeError, match="generation failed"):
        read({**valid, "status": "error", "error": "fixture failure"})
    with pytest.raises(ValueError, match="successful generated artifact"):
        read({**valid, "explanation": ""})


def collector_case(tag):
    job = {"job_id": "isolated-collector", "max_papers": 1, "created_at": 100}
    papers = [{"id": PAPER_ID, "title": "Original paper", "created_at": 50}]
    payload = {"schema": tag, "job_id": job["job_id"], "status": "completed",
               "papers": [{"paper_id": PAPER_ID, "action": "existing"}], "summary": "Original summary", "error": ""}
    return job, papers, payload


@pytest.mark.parametrize("namespace", ["tfi"])
def test_collector_result_alias_keeps_raw_payload_and_verified_results(namespace):
    job, papers, payload = collector_case(f"{namespace}.paper.collector.result.v1")
    original = copy.deepcopy((job, papers, payload))
    parsed, results, registered = paper_collector._validate_result(json.dumps(payload), job, {PAPER_ID}, papers)
    assert parsed == payload
    assert results == [{"paper_id": PAPER_ID, "title": "Original paper", "rank": 1, "action": "existing"}]
    assert registered == 0
    assert (job, papers, payload) == original


def test_collector_result_rejects_bad_tag_job_and_unverified_claims():
    for tag in bad_tags("paper.collector.result", 1):
        job, papers, payload = collector_case(tag)
        with pytest.raises(ValueError, match="schema/job identity mismatch"):
            paper_collector._validate_result(json.dumps(payload), job, {PAPER_ID}, papers)
    job, papers, payload = collector_case("tfi.paper.collector.result.v1")
    with pytest.raises(ValueError, match="schema/job identity mismatch"):
        paper_collector._validate_result(json.dumps({**payload, "job_id": "different"}), job, {PAPER_ID}, papers)
    with pytest.raises(PermissionError, match="currently authorized"):
        paper_collector._validate_result(json.dumps(payload), job, {PAPER_ID}, [])
    forged = {**payload, "papers": [{"paper_id": PAPER_ID, "action": "registered"}]}
    with pytest.raises(ValueError, match="pre-existing paper"):
        paper_collector._validate_result(json.dumps(forged), job, {PAPER_ID}, papers)


@pytest.mark.parametrize("namespace", ["tfi"])
def test_collector_contract_alias_still_requires_real_request_identity(monkeypatch, namespace):
    job = {"collector_contract": f"{namespace}.paper.collector.v2", "created_by": "fixture-user"}
    before = copy.deepcopy(job)
    backend = SimpleNamespace(_paper_collection_job_by_id=lambda job_id: job)
    monkeypatch.setattr(paper_collector, "current_operation_identity", lambda: None)
    with pytest.raises(PermissionError, match="original authenticated"):
        paper_collector.run("isolated-job", backend=backend)
    assert job == before


def test_collector_contract_rejects_bad_versions_before_actor_lookup(monkeypatch):
    monkeypatch.setattr(paper_collector, "require_actor", lambda *a: pytest.fail("invalid contract reached actor lookup"))
    for tag in bad_tags("paper.collector", 2):
        job = {"collector_contract": tag}
        with pytest.raises(RuntimeError, match="current execution contract"):
            paper_collector.run("isolated-job", backend=SimpleNamespace(_paper_collection_job_by_id=lambda job_id: job))
        assert job == {"collector_contract": tag}


@pytest.mark.parametrize("namespace", ["tfi"])
def test_collection_start_contract_keeps_real_identity_guard(monkeypatch, namespace):
    from management_portal import business_tasks

    monkeypatch.setattr(business_tasks, "start", forbid_write)
    monkeypatch.setattr(paper_collection_jobs, "_set_paper_collection_job", forbid_write)
    monkeypatch.setattr(paper_collector, "current_operation_identity", lambda: None)
    job = {"collector_contract": f"{namespace}.paper.collector.v2", "created_by": "fixture-user"}
    before = copy.deepcopy(job)
    with pytest.raises(PermissionError, match="original authenticated"):
        paper_collection_jobs._start_paper_collection_task(job)
    assert job == before


def test_collection_start_rejects_bad_contract_before_identity(monkeypatch):
    monkeypatch.setattr(paper_collector, "require_actor", lambda *a: pytest.fail("bad contract reached actor"))
    for tag in bad_tags("paper.collector", 2):
        job = {"collector_contract": tag}
        with pytest.raises(RuntimeError, match="current execution contract"):
            paper_collection_jobs._start_paper_collection_task(job)
        assert job == {"collector_contract": tag}


@pytest.fixture
def recovery_reader(monkeypatch):
    from management_portal import business_tasks

    job = {"job_id": "isolated-job", "created_by": "fixture-user", "status": "running",
           "collector_contract": "tfi.paper.collector.v2"}
    current = {**job, "existing_paper_ids": [], "process_owner": {"pid": "isolated-other-owner"},
               "invocation": {"schema": "tfi.paper-resident-input.v1",
                              "resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab",
                              "input_message_id": "msg_confirmed", "input_identity_status": "delivered"}}
    calls = []
    identity = SimpleNamespace(principal=SimpleNamespace(kind="user", id="fixture-user"))
    monkeypatch.setattr(paper_collector, "current_operation_identity", lambda: identity)
    monkeypatch.setattr(paper_collection_jobs.paper_state_store, "locked", lambda *a: nullcontext())
    monkeypatch.setattr(paper_collection_jobs.paper_state_store, "get", lambda *a: (calls.append("read"), current)[1])
    monkeypatch.setattr(paper_collection_jobs.paper_state_store, "merge", forbid_write)
    monkeypatch.setattr(paper_collection_jobs, "_start_paper_collection_task", forbid_write)
    monkeypatch.setattr(business_tasks, "is_active", lambda *a: False)
    monkeypatch.setattr(business_tasks, "process_identity", lambda: {"pid": "isolated-here"})
    monkeypatch.setattr(business_tasks, "process_is_alive", lambda owner: (calls.append("confirmed-owner"), True)[1])
    original_receipt = paper_projection.input_receipt

    def receipt(value):
        calls.append("receipt")
        return original_receipt(value)

    monkeypatch.setattr(paper_projection, "input_receipt", receipt)

    def read(actor="fixture-user"):
        before = copy.deepcopy((job, current))
        calls.clear()
        try:
            return paper_collection_jobs._recover_paper_collection_job(job, actor)
        finally:
            assert (job, current) == before

    return job, current, calls, read


@pytest.mark.parametrize("outer", ["tfi"])
@pytest.mark.parametrize("inner", ["tfi"])
def test_collection_recovery_accepts_each_contract_at_locked_read(recovery_reader, outer, inner):
    job, current, calls, read = recovery_reader
    job["collector_contract"] = f"{outer}.paper.collector.v2"
    current["collector_contract"] = f"{inner}.paper.collector.v2"
    assert read() is current
    assert calls == ["read", "receipt", "confirmed-owner"]


def test_collection_recovery_rejects_bad_outer_and_locked_contract(recovery_reader):
    job, current, calls, read = recovery_reader
    for tag in bad_tags("paper.collector", 2):
        job["collector_contract"] = tag
        assert read() is job
        assert calls == []
        job["collector_contract"] = "tfi.paper.collector.v2"
        current["collector_contract"] = tag
        assert read() is current
        assert calls == ["read"]


@pytest.mark.parametrize("namespace", ["tfi"])
def test_collection_recovery_retains_identity_state_and_confirmed_input_guards(recovery_reader, monkeypatch, namespace):
    job, current, calls, read = recovery_reader
    job["collector_contract"] = current["collector_contract"] = f"{namespace}.paper.collector.v2"
    baseline = copy.deepcopy((job, current))
    for row, expected_calls in ((job, []), (current, ["read"])):
        for field, value in (("created_by", "another-user"), ("deleted", True), ("status", "done")):
            row[field] = value
            assert read() is (job if row is job else current)
            assert calls == expected_calls
            job.clear()
            job.update(copy.deepcopy(baseline[0]))
            current.clear()
            current.update(copy.deepcopy(baseline[1]))
    for patch in ({"input_identity_status": "unconfirmed"}, {"input_message_id": ""},
                  {"resident_id": ""}, {"lineage_id": ""}, {"schema": "tfi.paper-resident-input.v999"}):
        current["invocation"] = {**baseline[1]["invocation"], **patch}
        assert read() is current
        assert calls == ["read", "receipt"]
    current["invocation"] = baseline[1]["invocation"]
    monkeypatch.setattr(paper_collector, "current_operation_identity", lambda: None)
    assert read() is job
    assert calls == []


@pytest.mark.parametrize("namespace", ["tfi"])
def test_input_receipt_alias_keeps_input_identity_and_original_dictionary(namespace):
    raw = {"schema": f"{namespace}.paper-resident-input.v1", "resident_id": "resident_0123456789ab",
           "lineage_id": "lineage_0123456789ab", "requested_message_id": "msg_original",
           "input_message_id": "msg_confirmed", "reply_message_ids": ["msg_reply", ""]}
    original = copy.deepcopy(raw)
    expected = {"schema": "tfi.paper-resident-input.v1", "resident_id": "resident_0123456789ab",
                "lineage_id": "lineage_0123456789ab", "requested_message_id": "msg_original",
                "input_message_id": "msg_confirmed", "reply_message_ids": ["msg_reply"],
                "event_id": "", "event_source_id": "", "effect_id": "", "trigger_path_id": "",
                "planned_message_id": "", "usage_message_id": "", "input_identity_status": "delivered"}
    assert paper_projection.input_receipt(raw) == expected
    assert raw == original
    unconfirmed = {**raw, "input_message_id": ""}
    assert paper_projection.input_receipt(unconfirmed)["input_identity_status"] == "unconfirmed"
    assert unconfirmed["input_message_id"] == ""


def test_input_receipt_rejects_bad_schema_without_changing_input():
    for tag in bad_tags("paper-resident-input", 1):
        receipt = {"schema": tag}
        original = copy.deepcopy(receipt)
        with pytest.raises(paper_projection.PaperInputIdentityError, match="requires schema"):
            paper_projection.input_receipt(receipt)
        assert receipt == original


def encrypted_fixture(tmp_path, tag):
    # Synthetic material only; never use a deployed key or real credential.
    cipher = Fernet(Fernet.generate_key())
    record = {"schema": tag, "actor": "fixture-user", "credential_id": "gitcred-fixture",
              "secret": "synthetic-test-only-secret", "updated_at": "2026-10-19T00:00:00Z"}
    plaintext = json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
    path = tmp_path / "gitcred-fixture.credential"
    path.write_bytes(cipher.encrypt(plaintext))
    path.chmod(0o600)
    return path, cipher, record, plaintext


@pytest.fixture
def no_credential_writes(monkeypatch):
    monkeypatch.setattr(credentials, "_write_record", forbid_write)
    monkeypatch.setattr(credentials, "_atomic_write", forbid_write)


@pytest.mark.parametrize("namespace", ["tfi"])
def test_encrypted_record_alias_keeps_authenticated_plaintext_and_token(tmp_path, no_credential_writes, namespace):
    path, cipher, record, plaintext = encrypted_fixture(tmp_path, f"{namespace}.repo-git-credential.v1")
    before = file_state(path)
    assert credentials._read_record(path, cipher) == record
    assert file_state(path) == before
    assert cipher.decrypt(path.read_bytes()) == plaintext


def test_encrypted_record_rejects_bad_schema_without_rewriting_token(tmp_path, no_credential_writes):
    for tag in bad_tags("repo-git-credential", 1):
        path, cipher, _, plaintext = encrypted_fixture(tmp_path, tag)
        before = file_state(path)
        with pytest.raises(credentials.RepoCredentialStoreError, match="unsupported schema"):
            credentials._read_record(path, cipher)
        assert file_state(path) == before
        assert cipher.decrypt(path.read_bytes()) == plaintext


@pytest.mark.parametrize("namespace", ["tfi"])
@pytest.mark.parametrize("defect", ["tampered", "wrong-key", "permissions"])
def test_schema_alias_never_bypasses_crypto_or_private_file_checks(tmp_path, no_credential_writes, namespace, defect):
    path, cipher, _, _ = encrypted_fixture(tmp_path, f"{namespace}.repo-git-credential.v1")
    error = "cannot be decrypted"
    if defect == "tampered":
        token = bytearray(base64.urlsafe_b64decode(path.read_bytes()))
        token[-1] ^= 1  # Break Fernet authentication, not its JSON schema.
        path.write_bytes(base64.urlsafe_b64encode(token))
    elif defect == "wrong-key":
        cipher = Fernet(Fernet.generate_key())
    else:
        path.chmod(0o644)
        error = "permissions are unsafe"
    before = file_state(path)
    with pytest.raises(credentials.RepoCredentialStoreError, match=error):
        credentials._read_record(path, cipher)
    assert file_state(path) == before


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
