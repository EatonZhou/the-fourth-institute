#!/usr/bin/env python3
"""RunScript steps for the Harness review Lineage (decision_fd760e898f9a)."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import traceback

EVOLUTION_SOURCES = (
    "management_portal/execution/harness_evolution.py",
    "management_portal/execution/harness_evolution_evaluation.py",
    "management_portal/execution/harness_evolution_offline.py",
    "management_portal/execution/harness_evolution_runner.py",
    "management_portal/execution/harness_policy_table.py",
)
BATCH_SCHEMA = "tfi.harness-review-batch.v1"
RESULT_SCHEMA = "tfi.harness-review-results.v1"
RECORDING_SWITCH = "system:harness-recording:enabled"
TIMING_SWITCH = "system:request-timing:enabled"
_BOOTSTRAP_INSTALLATION: Path | None = None


def read_site_config() -> dict:
    global _BOOTSTRAP_INSTALLATION

    try:
        from management_common.site_config import read_site_config as read
    except ModuleNotFoundError as exc:
        if exc.name != "management_common":
            raise
        # A captured RunScript has no source package on sys.path. Bootstrap only
        # the import location with stdlib; the selected Release owns all helpers
        # and revalidates the site configuration below.
        installation = Path(__file__).resolve().parents[1]
        configured = os.environ.get("PORTAL_SITE_CONFIG", "").strip()
        site = Path(configured).expanduser() if configured else installation / "config/site.json"
        if not site.is_absolute():
            site = installation / site
        data = json.loads(site.read_text(encoding="utf-8"))
        deployment = data.get("deployment", {}) if isinstance(data, dict) else None
        settings = deployment.get("system_workflow", {}) if isinstance(deployment, dict) else None
        if not isinstance(settings, dict):
            raise ValueError("deployment.system_workflow must be an object")
        value = settings.get("harness_release_tree")
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "\0\n\r"):
            raise ValueError("deployment.system_workflow.harness_release_tree must be a directory")
        release = Path(value).expanduser()
        if release.anchor and not release.is_absolute():
            raise ValueError("Release directory must be fully qualified or relative to the installation")
        if not release.is_absolute():
            release = installation / release
        release = release.resolve()
        if not (release / "management_common/site_config.py").is_file():
            raise ValueError("Configured Release does not contain the site configuration reader")
        sys.path.insert(0, str(release))
        _BOOTSTRAP_INSTALLATION = installation
        # Keep a relative site-file setting anchored to the captured script's
        # installation when the Release reader takes over; never rewrite it.
        os.environ["PORTAL_SITE_CONFIG"] = str(site.resolve())
        from management_common.site_config import read_site_config as read
    return read()


def _configuration() -> dict:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict) or not isinstance(deployment.get("system_workflow", {}), dict):
        raise ValueError("deployment.system_workflow must be an object")
    return deployment.get("system_workflow", {})


def _deployment_path(key: str) -> Path:
    value = _configuration().get(key)
    if value is None or value == "":
        raise ValueError(f"deployment.system_workflow.{key} is not configured")
    from management_common.portal_paths import resolve_directory

    resolved = resolve_directory(value)
    # The import location is relative to the installation that selected it,
    # not to the resulting Release directory a second time. Other runtime
    # paths retain the selected Release's portal_paths semantics.
    if key == "harness_release_tree" and _BOOTSTRAP_INSTALLATION is not None:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return (_BOOTSTRAP_INSTALLATION / path).resolve()
    return resolved


def _lineage_id() -> str:
    value = _configuration().get("harness_recording_lineage_id")
    if not isinstance(value, str) or not re.fullmatch(r"lineage_[0-9a-f]{12}", value):
        raise ValueError("deployment.system_workflow.harness_recording_lineage_id must be configured with a Lineage ID")
    return value


def switch_operations(client_key: str) -> list[dict]:
    """New switch + read-only status Effect for an atomic ApplyLineageChanges.

    Install each switch in its configured system_workflow Lineage.
    Existing collection/evolution/retention graphs are
    preserved. Pause/Resume projects policy through the common Lineage owner;
    manually triggering this Event only reads its actual execution policy.
    """
    titles = {RECORDING_SWITCH: "Harness 记录开关", TIMING_SWITCH: "模型请求计时开关"}
    if client_key not in titles:
        raise ValueError("unknown execution workflow switch")
    field = "harness_recording_enabled" if client_key == RECORDING_SWITCH else "model_request_timing_enabled"
    prefix = client_key.removesuffix(":enabled")
    return [
        {"op": "create", "kind": "component", "ref": "switch", "value": {
            "kind": "event_source", "client_key": client_key, "title": titles[client_key],
            "config": {"event_type": "manual.triggered"},
        }},
        {"op": "create", "kind": "component", "ref": "status", "value": {
            "kind": "effect", "client_key": prefix + ":status", "title": titles[client_key] + "状态",
            "config": {"script": 'policy=$(GetExecutionSettings)\noutput enabled=${policy.settings.' + field + '}',
                       "endpoints": []},
        }},
        {"op": "create", "kind": "wire", "value": {
            "kind": "route", "client_key": prefix + ":status:route",
            "source": "$ref:switch", "source_port": "output",
            "target": "$ref:status", "target_port": "invoke",
        }},
    ]


def release_rules_current() -> bool:
    release_tree = _deployment_path("harness_release_tree")
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet", "main", "HEAD", "--", *EVOLUTION_SOURCES],
            cwd=release_tree, capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@contextmanager
def locked(path: Path):
    from management_common.file_lock import lock_stream

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream, lock_stream(stream):
        yield


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    temporary.replace(path)


def batch_path(state_dir: Path, batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not re.fullmatch(r"script_run_[0-9a-f]{24}", batch_id):
        raise ValueError("batch_id must identify the sampling ScriptRun")
    return state_dir / "reviews" / batch_id


def sample_review(batch_id: str, options: dict, *, state_dir: Path | None = None) -> dict:
    from management_portal.execution.harness_evolution_evaluation import sample_for_review

    limits = {"hours": (24, 168), "per_arm": (4, 24), "limit": (24, 100)}
    if set(options) - limits.keys():
        raise ValueError("unknown sampling option")
    values = {}
    for key, (default, maximum) in limits.items():
        value = options.get(key, default)
        if type(value) not in (int, float) or not 1 <= value <= maximum:
            raise ValueError(f"invalid sampling {key}")
        if key != "hours" and type(value) is not int:
            raise ValueError(f"{key} must be an integer")
        values[key] = value
    root = batch_path(state_dir if state_dir is not None else _deployment_path("harness_state_dir"), batch_id)
    with locked(root / "batch.lock"):
        path = root / "samples.json"
        if path.exists():
            batch = json.loads(path.read_text())
        else:
            now = time.time()
            batch = {"schema": BATCH_SCHEMA, "batch_id": batch_id, "created_at": now,
                     "samples": sample_for_review(since=now - values["hours"] * 3600,
                         until=now, per_arm=values["per_arm"], limit=values["limit"])}
            write_json(path, batch)
    return {"batch_id": batch_id, "samples_path": str(path),
            "results_path": str(root / "results.json"), "sample_count": len(batch["samples"])}


def validated_reviews(batch: dict, results: dict) -> list[dict]:
    # main selects the active Release import tree before this reader is called.
    from management_common.format_tags import schema_matches

    if not schema_matches(results.get("schema"), "tfi.harness-review-results.v1") or results.get("batch_id") != batch["batch_id"]:
        raise ValueError("review results do not match the sampling batch")
    reviews = results.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("reviews must be a list")
    allowed = {item["execution_id"] for item in batch["samples"]}
    seen = set()
    for row in reviews:
        if not isinstance(row, dict) or set(row) - {"execution_id", "result", "reason", "score"}:
            raise ValueError("invalid review fields")
        identifier = row.get("execution_id")
        if not isinstance(identifier, str) or identifier not in allowed or identifier in seen:
            raise ValueError("review must name a unique execution from the sample")
        seen.add(identifier)
        if row.get("result") not in ("pass", "fail"):
            raise ValueError("review result must be pass or fail")
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise ValueError("review reason must contain 1 to 512 characters")
        score = row.get("score")
        if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError("review score must be between zero and one")
    return reviews


def record_batch(batch_id: str, *, state_dir: Path | None = None) -> dict:
    from management_portal.execution.harness_effectiveness import HarnessEffectivenessStore
    from management_portal.execution.harness_evolution_evaluation import record_review

    root = batch_path(state_dir if state_dir is not None else _deployment_path("harness_state_dir"), batch_id)
    with locked(root / "batch.lock"):
        batch = json.loads((root / "samples.json").read_text())
        content = (root / "results.json").read_bytes()
        reviews = validated_reviews(batch, json.loads(content))
        digest = hashlib.sha256(content).hexdigest()
        receipt_path = root / "recorded.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt["results_sha256"] != digest:
                raise ValueError("this batch was already recorded with different results")
            return receipt
        # Use a dedicated writer so unrelated telemetry failures cannot affect
        # this batch; its health must confirm the queued writes reached storage.
        store = HarnessEffectivenessStore()
        try:
            for row in reviews:
                record_review(**row, store=store)
            if store.health()["write_failure_count"]:
                raise RuntimeError("quality ledger write failed; retry the unchanged results")
        finally:
            store.close()
        receipt = {"batch_id": batch_id, "results_sha256": digest, "recorded": len(reviews),
                   "skipped": len(batch["samples"]) - len(reviews), "recorded_at": time.time()}
        write_json(receipt_path, receipt)
        return receipt


def maintain(operation: str, *, state_dir: Path | None = None) -> dict:
    from management_portal.execution.harness_evolution_offline import collect_turn_calls
    from management_portal.execution.harness_evolution_runner import run_daily

    if state_dir is None:
        state_dir = _deployment_path("harness_state_dir")
    # Distinct ScriptRuns may queue on this host lock; a daily evaluation must
    # not be discarded just because an hourly collection is still active.
    with locked(state_dir / "maintenance.lock"):
        if operation == "collect":
            return collect_turn_calls(lookback_days=3.0)
        if operation != "daily":
            raise ValueError("unknown maintenance operation")
        proposals_enabled = release_rules_current()
        run_daily(no_proposals=not proposals_enabled, auto_promote=False,
                  report_path=str(state_dir / "latest-report.md"), status_path=str(state_dir / "status.json"))
        return {"proposals_enabled": proposals_enabled, "report_path": str(state_dir / "latest-report.md"),
                "status_path": str(state_dir / "status.json")}


def main() -> int:
    from portal_script import inputs, set_output

    try:
        context = inputs()
        if context["lineage_id"] != _lineage_id():
            raise ValueError("this script belongs to the Harness review Lineage")
        # RunScript captures this file; algorithm imports always use the active
        # Release tree, while credentials stay in the script process.
        sys.path.insert(0, str(_deployment_path("harness_release_tree")))
        if not os.environ.get("PORTAL_DATABASE_URL"):
            for line in _deployment_path("harness_database_env_file").read_text().splitlines():
                key, separator, value = line.removeprefix("export ").partition("=")
                if separator and key.strip() == "PORTAL_DATABASE_URL":
                    os.environ[key.strip()] = shlex.split(value)[0]
                    break
            if not os.environ.get("PORTAL_DATABASE_URL"):
                raise ValueError("Configured database environment file does not provide PORTAL_DATABASE_URL")
        operation, = sys.argv[1:]
        if operation == "sample":
            prompt = context["arguments"].get("prompt", "")
            options = json.loads(prompt) if prompt.strip() else {}
            result = sample_review(context["script_run_id"], options)
        elif operation == "record":
            result = record_batch(context["arguments"]["prompt"].strip())
        else:
            result = maintain(operation)
        set_output({"operation": operation, **result})
        return 0
    except Exception as exc:
        print(f"Harness Lineage step failed ({type(exc).__name__})", file=sys.stderr)
        for frame, line in traceback.walk_tb(exc.__traceback__):
            print(f"  {frame.f_code.co_filename}:{line} in {frame.f_code.co_name}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
