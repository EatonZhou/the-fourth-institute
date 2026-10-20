"""Capacity alert policy and durable notification delivery over public ISA.

Collection belongs to GetRuntimeCapacity. Lineage owns the schedule and switches;
this workflow owns only observations, hysteresis and its notification outbox.
"""
from __future__ import annotations

from management_common.format_tags import schema_matches

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import math


STATE_KEY = "runtime_capacity_alerts"
SCHEMA = "tfi.capacity-alerts.v1"
DEFAULT_POLICY = {
    "database": {"warning": 80, "critical": 90, "recovery": 70},
    "disk": {"warning": 85, "critical": 95, "recovery": 80},
    "queue": {"warning": 20, "critical": 50, "recovery": 5},
    "confirmation_seconds": 300,
    "queue_confirmation_seconds": 900,
    "max_sample_gap_seconds": 900,
}
LEVELS = {"normal": 0, "warning": 1, "critical": 2}


def workflow_operations(script_path, recipients):
    """An explicit install plan; the caller submits it once to the chosen Lineage."""
    import json
    from management_portal.society_service.lineage_effect_script import literal_argument

    arguments = json.dumps({"recipients": recipients, "policy": DEFAULT_POLICY}, ensure_ascii=False, separators=(",", ":"))
    script = (f"run=$(RunScript {literal_argument('path', str(script_path))} runtime=python "
              f"timeout_seconds:=540 exclusive_key=runtime-capacity-alerts arguments:={arguments})\n"
              'output script_run_id="${run.script_run_id}" status="${run.status}"')
    components = [
        ("timer", "event_source", "容量检查（每 5 分钟）", {
            "event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "interval",
            "interval_seconds": 300, "jitter_seconds": 0, "misfire_policy": "coalesce_latest",
            "overlap_policy": "drop", "max_fires": None,
        }),
        ("manual", "event_source", "立即检查运行容量", {"event_type": "manual.triggered", "max_fires": None}),
        ("effect", "effect", "检查容量并通知", {"script": script, "endpoints": [], "failure_recipients": recipients}),
    ]
    operations = [{"op": "create", "kind": "component", "ref": role, "value": {
        "kind": kind, "title": title, "client_key": f"system:capacity-alerts:{role}", "config": config,
    }} for role, kind, title, config in components]
    operations.extend({"op": "create", "kind": "wire", "value": {
        "kind": "route", "source": "$ref:" + role, "source_port": "output",
        "target": "$ref:effect", "target_port": "invoke", "client_key": f"system:capacity-alerts:{role}:invoke",
        "config": {"unavailable_policy": "drop", "expires_after_seconds": 0},
    }} for role in ("timer", "manual"))
    return operations


def policy_with(overrides=None):
    policy = deepcopy(DEFAULT_POLICY)
    for key, value in (overrides or {}).items():
        if key not in policy:
            raise ValueError(f"Unknown capacity policy field: {key}")
        if isinstance(policy[key], dict):
            if not isinstance(value, dict) or set(value) - set(policy[key]):
                raise ValueError("Invalid capacity thresholds")
            policy[key].update(value)
        else:
            policy[key] = value
    for kind in ("database", "disk", "queue"):
        values = policy[kind]
        for value in values.values():
            _number(value)
        if not 0 <= values["recovery"] < values["warning"] < values["critical"]:
            raise ValueError("Capacity thresholds must satisfy recovery < warning < critical")
    if not 1 <= _number(policy["confirmation_seconds"]) < _number(policy["max_sample_gap_seconds"]):
        raise ValueError("Invalid confirmation and sampling intervals")
    if _number(policy["queue_confirmation_seconds"]) < policy["confirmation_seconds"]:
        raise ValueError("Queue confirmation must be at least the capacity confirmation interval")
    return policy


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError("Capacity observations must be finite nonnegative numbers")
    return value


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Capacity observation timestamp must include timezone")
    return parsed.timestamp()


def _samples(snapshot):
    """Unavailable sources never manufacture zero capacity or a recovery."""
    samples = {}
    labels = {"database": "数据库连接", "disks": "磁盘空间", "execution_queue": "执行队列"}
    for source, label in labels.items():
        data = snapshot[source]
        if data.get("status") not in {"ok", "unavailable"}:
            raise ValueError("Unknown capacity source status")
        available = data["status"] == "ok"
        samples[f"availability:{source}"] = {
            "kind": "availability", "label": label + "采集", "value": 0 if available else 1,
            "unit": "", "detail": "可读取" if available else "暂不可读取，既有容量告警保留",
        }
        if not available:
            continue
        if source == "database":
            samples["database"] = {"kind": "database", "label": label,
                "value": _number(data["used_percent"]), "unit": "%",
                "detail": f"连接 {data['connections']} / 普通连接槽 {data['usable_connections']}"}
        elif source == "disks":
            if not data["filesystems"]:
                raise ValueError("A successful disk observation must contain filesystems")
            for disk in data["filesystems"]:
                key = "disk:" + disk["key"]
                if key in samples:
                    raise ValueError("Duplicate filesystem observation")
                samples[key] = {"kind": "disk", "label": "磁盘 " + disk["path"],
                    "value": _number(disk["used_percent"]), "unit": "%",
                    "detail": f"可用 {disk['free_bytes'] / 1024**3:.1f} GiB"}
        else:
            # Backlog is an observation, not proof of a capacity bottleneck.
            eligible = data.get("eligible_queued_count")
            count = data["queued_count"] if eligible is None else eligible
            samples["queue"] = {"kind": "queue", "label": label + "积压",
                "value": _number(count), "unit": " 项",
                "detail": ("可调度排队" if eligible is not None else "排队总量（包含主动暂停和额度等待）")
                    + f"；运行中 {data['active_count']}，总并发 {data['max_concurrency']}"}
    return samples


def advance(previous, snapshot, *, lineage_id, recipients, policy):
    if not schema_matches(snapshot.get("schema"), "tfi.runtime-capacity.v1"):
        raise ValueError("Unsupported runtime capacity schema")
    if previous and not schema_matches(previous.get("schema"), "tfi.capacity-alerts.v1"):
        raise ValueError("Unsupported capacity alert state")
    observed = snapshot["observed_at"]
    now = _timestamp(observed)
    state = deepcopy(previous) if previous else {"schema": SCHEMA, "sequence": 0, "metrics": {}, "outbox": []}
    if state.get("observed_at") and now <= _timestamp(state["observed_at"]):
        return state
    samples = _samples(snapshot)
    for key, record in state["metrics"].items():
        if key not in samples:
            record["pending"] = None
            record["available"] = False
    for key, sample in samples.items():
        value, kind = sample["value"], sample["kind"]
        record = state["metrics"].setdefault(key, {"level": "normal", "pending": None})
        level = record["level"]
        thresholds = {"warning": 1, "critical": 2, "recovery": 0} if kind == "availability" else policy[kind]
        candidate = ("critical" if value >= thresholds["critical"] else
                     "warning" if value >= thresholds["warning"] else "normal")
        # A current alert stays at its highest level until the recovery boundary.
        if LEVELS[candidate] < LEVELS[level] and value > thresholds["recovery"]:
            candidate = level
        pending = record.get("pending")
        if record.get("observed_at") and now - _timestamp(record["observed_at"]) > policy["max_sample_gap_seconds"]:
            pending = None
        if candidate == level:
            pending = None
        elif not pending or pending["level"] != candidate:
            pending = {"level": candidate, "since": observed}
        immediate = candidate == "critical" and kind in {"database", "disk"}
        duration = policy["queue_confirmation_seconds"] if kind == "queue" and candidate != "normal" else policy["confirmation_seconds"]
        if pending and (immediate or now - _timestamp(pending["since"]) >= duration):
            record["level"] = candidate
            pending = None
            state["sequence"] += 1
            notification_id = "capacity_" + sha256(f"{lineage_id}:{state['sequence']}:{key}".encode()).hexdigest()[:32]
            recovered = candidate == "normal"
            title = ("运行容量恢复：" if recovered else "运行容量预警：") + sample["label"]
            state["outbox"].append({
                "notification_id": notification_id, "lineage_id": lineage_id,
                "recipients": list(recipients), "title": title,
                "severity": "success" if recovered else "error" if candidate == "critical" else "warning",
                "message": (f"{sample['label']}：{value:g}{sample['unit']}。{sample['detail']}。\n"
                    f"{'已回到恢复阈值内' if recovered else '已达到告警阈值'}"
                    f"（预警 {thresholds['warning']:g}，紧急 {thresholds['critical']:g}，恢复 ≤{thresholds['recovery']:g}）；"
                    f"观察时间 {observed}。\n"
                    f"Portal System：/lineage?lineage={lineage_id}"),
            })
        record.update(sample, observed_at=observed, pending=pending, available=True)
    state.update(observed_at=observed, snapshot=deepcopy(snapshot), policy=deepcopy(policy))
    return state


def _read(isa, lineage_id):
    return isa("GetSharedState", lineage_id=lineage_id, key=STATE_KEY)["value"]


def _cas(isa, lineage_id, old, new):
    return isa("UpdateSharedState", lineage_id=lineage_id, key=STATE_KEY,
               expected_value=old, value=new)["matched"]


def drain(isa, lineage_id):
    """Commit the outbox before send; a lost acknowledgement reuses the notice ID."""
    delivered = []
    for _ in range(100):
        state = _read(isa, lineage_id)
        if not state or not state["outbox"]:
            return delivered
        notice = state["outbox"][0]
        isa("CreateNotification", **notice)
        updated = deepcopy(state)
        updated["outbox"].pop(0)
        if _cas(isa, lineage_id, state, updated):
            delivered.append(notice["notification_id"])
    raise RuntimeError("Capacity notification outbox did not converge")


def run_once(isa, *, lineage_id, recipients, overrides=None):
    if not lineage_id or not recipients or any(not isinstance(x, str) or not x.strip() for x in recipients):
        raise ValueError("Capacity monitoring requires lineage and explicit human recipients")
    policy = policy_with(overrides)
    delivered = drain(isa, lineage_id)
    snapshot = isa("GetRuntimeCapacity")["capacity"]
    for _ in range(8):
        previous = _read(isa, lineage_id)
        updated = advance(previous, snapshot, lineage_id=lineage_id, recipients=recipients, policy=policy)
        if previous == updated or _cas(isa, lineage_id, previous, updated):
            break
    else:
        raise RuntimeError("Concurrent capacity observations did not converge")
    delivered.extend(drain(isa, lineage_id))
    final = _read(isa, lineage_id)
    return {"observed_at": final["observed_at"], "snapshot": final["snapshot"],
        "alerts": {key: value["level"] for key, value in final["metrics"].items() if value["level"] != "normal"},
        "notifications": delivered, "pending_notifications": len(final["outbox"])}
