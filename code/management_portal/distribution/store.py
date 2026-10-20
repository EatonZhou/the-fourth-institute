"""Durable release inputs and runs. IDs here are local operation identities."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from functools import lru_cache

from management_common.domain_database import DomainDatabase

DDL = """
CREATE TABLE IF NOT EXISTS configurations (
 config_id TEXT PRIMARY KEY, name TEXT NOT NULL,
 snapshot JSONB NOT NULL, provenance JSONB NOT NULL, created_by TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE configurations DROP COLUMN IF EXISTS revision;
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, actor TEXT NOT NULL, request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL, config_id TEXT,
 snapshot JSONB NOT NULL, provenance JSONB NOT NULL, mode TEXT NOT NULL,
 tau_validation BOOLEAN NOT NULL, responsible_resident_id TEXT NOT NULL,
 supervisor_resident_id TEXT NOT NULL, state TEXT NOT NULL, result JSONB NOT NULL,
 revision BIGINT NOT NULL DEFAULT 1, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(actor,request_key));
CREATE TABLE IF NOT EXISTS stages (
 run_id TEXT NOT NULL REFERENCES runs(run_id), sequence BIGINT NOT NULL,
 stage TEXT NOT NULL, evidence JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,sequence));
CREATE TABLE IF NOT EXISTS config_requests (
 actor TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,response JSONB NOT NULL,
 PRIMARY KEY(actor,request_key));
CREATE TABLE IF NOT EXISTS page_preferences(actor TEXT PRIMARY KEY,value JSONB NOT NULL);
"""
RESPONSIBLE = "resident_40987d147438"
SUPERVISOR = "resident_e4f55ad12dae"
STATES = {"awaiting_supervisor", "resolving", "generating", "generated", "installing",
          "checking", "succeeded", "failed", "needs_configuration"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def record(row):
    if row is None:
        raise KeyError("Release record does not exist")
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in dict(row).items()}


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase("distribution", "distribution", DDL)


def database():
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


class ReleaseStore:
    def save_config(self, *, name, snapshot, actor, config_id=None, provenance=None, request_key=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("Configuration name must contain 1–120 characters")
        if not isinstance(snapshot, dict):
            raise ValueError("Configuration snapshot must be an object")
        identity = config_id or uuid.uuid4().hex
        fingerprint = digest({"name": name, "snapshot": snapshot, "config_id": config_id, "provenance": provenance})
        if request_key is not None and (not isinstance(request_key, str) or not request_key or len(request_key) > 200):
            raise ValueError("Invalid configuration request key")
        with database().transaction("config-request:" + actor + ":" + request_key if request_key else "config:" + identity) as c:
            if request_key:
                receipt = c.execute("SELECT request_hash,response FROM config_requests WHERE actor=%s AND request_key=%s", (actor, request_key)).fetchone()
                if receipt:
                    if receipt["request_hash"] != fingerprint:
                        raise ValueError("Configuration request key reused with different inputs")
                    return {key: value for key, value in receipt["response"].items() if key != "revision"}
            from management_common.distribution_targets import normalize_targets
            from .features import normalize_snapshot
            snapshot = normalize_snapshot(snapshot)
            from .resource_dependencies import freeze
            snapshot = freeze(snapshot)
            snapshot = {**snapshot, 'target_platforms': normalize_targets(snapshot.get('target_platforms', ['linux']))}
            old = c.execute("SELECT * FROM configurations WHERE config_id=%s FOR UPDATE", (identity,)).fetchone()
            if config_id and old is None:
                raise KeyError("Configuration does not exist")
            if old:
                c.execute("UPDATE configurations SET name=%s,snapshot=%s::jsonb,updated_at=now() WHERE config_id=%s",
                          (name.strip(), canonical(snapshot), identity))
            else:
                c.execute("INSERT INTO configurations(config_id,name,snapshot,provenance,created_by) VALUES (%s,%s,%s::jsonb,%s::jsonb,%s)",
                          (identity, name.strip(), canonical(snapshot), canonical(provenance or {}), actor))
            response = record(c.execute("SELECT * FROM configurations WHERE config_id=%s", (identity,)).fetchone())
            if request_key:
                c.execute("INSERT INTO config_requests VALUES(%s,%s,%s,%s::jsonb)", (actor, request_key, fingerprint, canonical(response)))
            return response

    def get_config(self, config_id):
        with database().read() as c:
            return record(c.execute("SELECT * FROM configurations WHERE config_id=%s", (config_id,)).fetchone())

    def list_configs(self):
        with database().read() as c:
            return [record(row) for row in c.execute("SELECT * FROM configurations ORDER BY updated_at DESC,config_id").fetchall()]

    def create_run(self, *, actor, request_key, mode, snapshot=None, config_id=None,
                   tau_validation=False, provenance=None):
        if mode not in {"publish", "full_test"}:
            raise ValueError("mode must be publish or full_test")
        if not isinstance(request_key, str) or not request_key or len(request_key) > 200:
            raise ValueError("A per-request idempotency key is required")
        if not isinstance(tau_validation, bool):
            raise ValueError("tau_validation must be boolean")
        request_hash = digest({"config_id": config_id, "snapshot": snapshot, "mode": mode,
                               "tau_validation": tau_validation, "provenance": provenance})
        with database().transaction("request:" + actor + ":" + request_key) as c:
            old = c.execute("SELECT * FROM runs WHERE actor=%s AND request_key=%s", (actor, request_key)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ValueError("Idempotency key reused with different release inputs")
                return {**record(old), "replayed": True}
            config = None
            if config_id:
                config = record(c.execute("SELECT * FROM configurations WHERE config_id=%s FOR SHARE", (config_id,)).fetchone())
            # Each new run freezes the currently saved content under the row lock.
            selected = snapshot if snapshot is not None else config["snapshot"] if config else None
            if not isinstance(selected, dict):
                raise ValueError("A configuration or snapshot is required")
            from management_common.distribution_targets import normalize_targets
            target_origin = 'explicit' if 'target_platforms' in selected else 'legacy-effective-linux'
            from .features import normalize_snapshot
            selected = normalize_snapshot(selected)
            from .resource_dependencies import freeze
            # A named configuration already pins its reviewed relationships.
            # Raw client snapshots are always resolved server-side.
            if snapshot is not None or 'resource_dependencies' not in selected:
                selected = freeze(selected)
            selected = {**selected, 'target_platforms': normalize_targets(selected.get('target_platforms', ['linux']))}
            run_id = uuid.uuid4().hex
            source = {"configuration": config_id,
                      "source": selected.get("source"), **(provenance or {}), 'target_origin': target_origin}
            c.execute("""INSERT INTO runs(run_id,actor,request_key,request_hash,config_id,
                snapshot,provenance,mode,tau_validation,responsible_resident_id,supervisor_resident_id,state,result)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,'{}'::jsonb)""",
                (run_id, actor, request_key, request_hash, config_id,
                 canonical(selected), canonical(source), mode, tau_validation, RESPONSIBLE,
                 SUPERVISOR if mode == "full_test" else "", "awaiting_supervisor" if mode == "full_test" else "resolving"))
            return {**record(c.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone()), "replayed": False}

    def get_run(self, run_id):
        with database().read() as c:
            run = record(c.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone())
            run["stages"] = [record(row) for row in c.execute("SELECT * FROM stages WHERE run_id=%s ORDER BY sequence", (run_id,)).fetchall()]
            return run

    def list_runs(self, config_id=None):
        with database().read() as c:
            return [record(row) for row in c.execute("SELECT * FROM runs WHERE (%s::text IS NULL OR config_id=%s) ORDER BY created_at DESC LIMIT 100", (config_id, config_id)).fetchall()]

    def report(self, run_id, *, state, stage, evidence, expected_revision):
        """Internal worker/supervisor API, deliberately absent from browser API."""
        if state not in STATES or not isinstance(evidence, dict):
            raise ValueError("Invalid release stage report")
        with database().transaction("run:" + run_id) as c:
            run = record(c.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone())
            if expected_revision != run["revision"]:
                raise ValueError("Run revision changed")
            if run["state"] == "succeeded":
                raise ValueError("A completed run is immutable; start a new run")
            if state == "succeeded":
                if not evidence.get("manifest") or not evidence.get("checks_passed"):
                    raise ValueError("Success requires artifact manifest and passing checks")
                if run["mode"] == "full_test":
                    if evidence.get("supervisor_resident_id") != run["supervisor_resident_id"] or not evidence.get("instance_url") or not evidence.get("behavior_evidence"):
                        raise ValueError("Full test requires actual supervisor and installed behavior evidence")
                    if run["tau_validation"] and evidence.get("tau_status") != "passed":
                        raise ValueError("Requested TAU validation has not passed")
            revision = run["revision"] + 1
            c.execute("INSERT INTO stages(run_id,sequence,stage,evidence) VALUES (%s,%s,%s,%s::jsonb)",
                      (run_id, revision, stage, canonical(evidence)))
            c.execute("UPDATE runs SET state=%s,result=%s::jsonb,revision=%s,updated_at=now() WHERE run_id=%s",
                      (state, canonical(evidence), revision, run_id))
        return self.get_run(run_id)
