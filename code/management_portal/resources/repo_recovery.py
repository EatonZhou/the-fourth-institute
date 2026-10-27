"""Explicit recovery of a lost Repo registration from a retained resolve receipt.

The caller supplies the historical identity, exact checkout moves and CAS
preimages of duplicate registrations. No alias, path guessing or scan-wide
rewrite is performed. Registry changes and the recovery receipt commit together.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from management_portal import permission_system, portal_features, resource_identity_backend as repos
from .providers.common import canonical_record
from .registry_store import announce_registry_changes, get_registry_store
from .retired_identity_boundary import reject_retired

RECOVERIES = "repo_registration_recoveries"


def record_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _absolute(value: Any) -> str:
    if not isinstance(value, str) or not value or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("Checkout move requires an absolute path without traversal")
    return value


def _retained_revision(service, resource_id: str, proof: dict, *, actor: str) -> dict:
    """A successful frozen build can outlive a stale cached registration HEAD."""
    address = str(proof.get("address") or "")
    data, _content_type, _metadata = service.read_content(address, actor=actor, max_bytes=1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    if digest != proof.get("sha256"):
        raise ValueError("Revision evidence SHA256 changed")
    evidence = json.loads(data)
    record = evidence.get("result", {}).get("resource", {})
    target = evidence.get("result", {}).get("target", {})
    application = record.get("application") or {}
    build = record.get("application_build") or {}
    commit = str(application.get("commit") or "")
    if (evidence.get("schema") != "tfi.hatg-instruction-result.v2"
            or evidence.get("instruction") != "ResolveResource"
            or evidence.get("status") != "committed" or not evidence.get("instruction_id")
            or (record.get("type") or target.get("resource_type")) != "workbench"
            or not re.fullmatch(r"workbench_[0-9a-f]{12}", str(record.get("resource_id") or ""))
            or application.get("repo_id") != resource_id
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or build.get("status") != "ready" or build.get("commit") != commit):
        raise ValueError("Revision evidence must bind the original Repo to a successful frozen build")
    return {"address": address, "sha256": digest, "commit": commit,
            "source_instruction_id": evidence["instruction_id"],
            "workbench_id": record["resource_id"]}


def restore_registration(service, resource_id: str, payload: dict, *, actor: str) -> dict:
    if not permission_system.allows(actor, "UpdateResource", {"resource_id": resource_id}):
        raise PermissionError("Repo recovery requires permission to update its original identity")
    if not str(payload.get("reason") or "").strip():
        raise ValueError("Repo recovery requires an explicit reason")
    address = str(payload.get("evidence_address") or "")
    data, _content_type, _metadata = service.read_content(address, actor=actor, max_bytes=1024 * 1024)
    evidence_hash = hashlib.sha256(data).hexdigest()
    if evidence_hash != payload.get("evidence_sha256"):
        raise ValueError("Recovery evidence SHA256 changed")
    evidence = json.loads(data)
    if (evidence.get("schema") != "tfi.hatg-instruction-result.v2"
            or evidence.get("instruction") != "ResolveResource"
            or evidence.get("status") != "committed"
            or not evidence.get("instruction_id")):
        raise ValueError("Recovery requires a retained successful ResolveResource receipt")
    source = evidence.get("result", {}).get("resource", {})
    if (source.get("resource_id") != resource_id or source.get("schema") != repos.REGISTRY_SCHEMA
            or source.get("type") != "repo" or source.get("status") != "active"
            or not source.get("created_at")):
        raise ValueError("Historical Repo identity does not match recovery target")
    previous = {**source, "id": resource_id}
    previous.pop("resource_id", None)
    repos._validated_repo(resource_id, previous)
    repos.require_git_source(previous)

    moves = payload.get("checkout_moves")
    if not isinstance(moves, list) or not moves:
        raise ValueError("Recovery requires exact checkout_moves")
    mapping = {}
    for move in moves:
        old, new = _absolute(move["from"]), _absolute(move["to"])
        if old in mapping:
            raise ValueError("Duplicate source checkout")
        mapping[old] = new
    if set(mapping) != set(repos.repo_checkout_paths(previous)):
        raise ValueError("Checkout moves must cover exactly the historical checkout paths")
    group = [repos.inspect_git_checkout(path) for path in mapping.values()]
    if len({item["path"] for item in group}) != len(group):
        raise ValueError("Checkout moves must have distinct destinations")
    by_path = {raw: inspected for raw, inspected in zip(mapping.values(), group)}
    revision_proof = payload.get("revision_evidence")
    if revision_proof is not None and not isinstance(revision_proof, dict):
        raise ValueError("revision_evidence must contain address and sha256")
    revision = (_retained_revision(service, resource_id, revision_proof, actor=actor)
                if revision_proof is not None else None)
    if revision:
        for checkout in group:
            repos._run_git(Path(checkout["path"]), "cat-file", "-e", revision["commit"] + "^{commit}")
    unavailable_heads = []
    for old_checkout in previous.get("checkouts") or []:
        current = by_path[mapping[old_checkout["path"]]]
        remote = str(old_checkout.get("identity_remote") or "")
        if remote and current["identity_remote"] != remote:
            raise ValueError("Moved checkout has a different identity remote")
        head = str(old_checkout.get("head") or "")
        if head:
            # A retained commit proves common history; detached/new branches
            # need not currently descend from that particular historical head.
            try:
                repos._run_git(Path(current["path"]), "cat-file", "-e", head + "^{commit}")
            except ValueError:
                if not revision:
                    raise
                unavailable_heads.append(head)
    if len(repos.group_logical_repos(group)) != 1:
        raise ValueError("Recovery destinations are not one logical Git repository")
    article = portal_features.contribution("resource.article_registration")
    if article and any(article.is_storage_path(item["path"]) for item in group):
        raise ValueError("Article storage cannot be recovered as a Repo")

    expected = {}
    replacements = payload.get("replacements", [])
    if not isinstance(replacements, list):
        raise ValueError("Recovery replacements must be a list")
    for replacement in replacements:
        rid = replacement["resource_id"]
        if rid == resource_id or rid in expected or not repos._is_repo_id(rid):
            raise ValueError("Invalid replacement identity")
        if not permission_system.allows(actor, "UnregisterResource", {"resource_id": rid}):
            raise PermissionError("Repo recovery requires permission to remove each duplicate")
        expected[rid] = replacement["expected_sha256"]

    fingerprint = record_digest({"resource_id": resource_id, "payload": payload, "actor": actor})
    receipt_id = resource_id + ":" + fingerprint
    store = get_registry_store()
    changed = False
    from management_portal.resource_auto_registration import IGNORE_COLLECTION

    with store.writer(repos.REPO_COLLECTION, RECOVERIES, IGNORE_COLLECTION) as writer:
        records = repos.REPO_REGISTRY.records(writer)
        receipt = writer.get(RECOVERIES, receipt_id)
        if receipt:
            result = records.get(resource_id)
            if (result is None or record_digest(result) != receipt["restored_sha256"]
                    or any(rid in records for rid in expected)):
                raise ValueError("Previously recovered registration has changed")
        else:
            if resource_id in records:
                raise ValueError("Original Repo registration already exists; recovery will not overwrite it")
            if any(entry.get("resource_type") == "repo" and entry.get("resource_id") == resource_id
                   for entry in writer.documents(IGNORE_COLLECTION).values()):
                raise ValueError("Explicitly unregistered Repo requires a separate lifecycle decision")
            keys = {key for checkout in group for key in repos._checkout_match_keys(checkout)}
            conflicts = {rid for rid, record in records.items()
                         if keys.intersection(repos._record_match_keys(record))}
            if conflicts != set(expected):
                raise ValueError("Recovery duplicate identities do not match current checkout owners")
            for rid, digest in expected.items():
                current = records[rid]
                if record_digest(canonical_record(rid, current)) != digest:
                    raise ValueError("Replacement Repo CAS preimage changed")
                # Never discard another usable checkout or an exported-tree identity.
                repos.require_git_source(current)
                if not {str(Path(p).resolve()) for p in repos.repo_checkout_paths(current)}.issubset(
                        {item["path"] for item in group}):
                    raise ValueError("Duplicate Repo contains checkouts outside this recovery")
            prior_without_locations = {**previous, "checkouts": []}
            result = repos._merge_record(prior_without_locations, resource_id, group, [])
            result["canonical_checkout"] = by_path[mapping[previous["canonical_checkout"]]]["path"]
            receipt = {
                "schema": "tfi.repo-registration-recovery.v1", "resource_id": resource_id,
                "actor": actor, "reason": payload["reason"], "recovered_at": result["updated_at"],
                "evidence_address": address, "evidence_sha256": evidence_hash,
                "source_instruction_id": evidence["instruction_id"], "source_record": source,
                "checkout_moves": moves, "replaced_records": {rid: records[rid] for rid in expected},
                "restored_sha256": record_digest(result),
                "revision_evidence": revision, "unavailable_historical_heads": unavailable_heads,
            }
            # Explicit retirement is stronger than a historical registration.
            # Any error rolls back all replacements and the recovery receipt.
            reject_retired(writer.connection, store.database.schema, [resource_id])
            repos._put_repo(writer, result)
            for rid in expected:
                writer.delete(repos.REPO_COLLECTION, rid)
            writer.put(RECOVERIES, receipt_id, receipt)
            changed = True
    if changed:
        announce_registry_changes(repos.REPO_COLLECTION,
                                  {resource_id: "created", **{rid: "deleted" for rid in expected}},
                                  resource_type="repo")
    # Reuse the service's normal permission/index cleanup. It is replayable if
    # projection fails after commit; registry content is never written twice.
    for rid in expected:
        service._complete_mutation(
            {"resource_id": rid, "deleted": True}, resource_type="repo", expected_id=rid,
            actor=actor, kind="delete", wait_for_index=False, index_timeout=45.0)
    return result
