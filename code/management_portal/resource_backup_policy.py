"""Resource Backup planning as the overview shows it: the configured policy and profiles,
retention, verification and the schedule's timers."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from management_portal.backup_lineage import BACKUP_OPERATIONS, VERIFICATION_OPERATIONS

from management_portal.resource_backup_values import (
    BACKUP_LABELS, _mapping, _string, _number, _boolean, _timestamp, _health_status, _named_record,
)


def _configuration_projection(
    runtime_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Select display metadata without carrying secret references forward.

    ``target``, ``coverage`` and ``policy`` are optional display sections
    of the service's config.yaml, next to its profiles and Restic settings.
    """

    projected: dict[str, Any] = {}
    agent_config = runtime_config
    agent_backup = _mapping(agent_config.get("backup"))
    agent_policy = _mapping(
        agent_config.get("policy") or agent_backup.get("policy")
    )
    runtime_profiles = _mapping(runtime_config.get("profiles"))
    projected["policy"] = _configured_policy(runtime_config, agent_policy, runtime_profiles)
    target = _configured_target(runtime_config, agent_config, agent_backup)
    if any(value not in {"", False} for value in target.values()):
        projected["target"] = target

    raw_coverage = (
        agent_config.get("coverage")
        or agent_backup.get("coverage")
    )
    if raw_coverage:
        projected["coverage"] = raw_coverage
    elif runtime_profiles:
        projected["coverage"] = _configured_coverage(runtime_profiles)
    return projected


def _configured_policy(
    runtime_config: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
    runtime_profiles: Mapping[str, Any],
) -> dict[str, Any]:
    """The default profile, each profile's sources and the retention the service is configured with."""
    policy: dict[str, Any] = {
        "default_profile": _string(
            agent_policy.get("default_profile"),
            limit=40,
        ),
    }
    agent_profiles = agent_policy.get("profiles")
    profile_projection: dict[str, dict[str, Any]] = {}
    for profile in BACKUP_OPERATIONS:
        agent_profile = _named_record(
            agent_profiles,
            profile,
        )
        runtime_profile = _mapping(runtime_profiles.get(profile))
        sources = runtime_profile.get("sources")
        source_records = sources if isinstance(sources, list) else []
        excludes = runtime_profile.get("excludes")
        profile_projection[profile] = {
            "description": _string(
                runtime_profile.get("description")
                or agent_profile.get("description"),
                limit=240,
            ),
            "source_count": len(source_records),
            "required_source_count": sum(
                1
                for item in source_records
                if _boolean(_mapping(item).get("required"), True)
            ),
            "exclude_count": (
                len(excludes)
                if isinstance(excludes, list)
                else 0
            ),
        }
    policy["profiles"] = profile_projection

    maintenance = _mapping(runtime_config.get("maintenance"))
    retention = _mapping(
        maintenance.get("retention")
        or agent_policy.get("retention")
    )
    policy["retention"] = {
        key: _number(retention.get(key))
        for key in (
            "keep_hourly",
            "keep_daily",
            "keep_weekly",
            "keep_monthly",
            "keep_yearly",
            "keep_last",
        )
        if retention.get(key) is not None
    }
    return policy


def _configured_target(
    runtime_config: Mapping[str, Any],
    agent_config: Mapping[str, Any],
    agent_backup: Mapping[str, Any],
) -> dict[str, Any]:
    """The backup target as configured; a Restic repository shows as its path."""
    agent_target = _mapping(
        agent_config.get("target") or agent_backup.get("target")
    )
    runtime_target = _mapping(runtime_config.get("target"))
    restic = _mapping(runtime_config.get("restic"))
    repository = _string(restic.get("repository"), limit=1200)
    target_source = runtime_target or agent_target
    target: dict[str, Any] = {
        "label": _string(
            target_source.get("label")
            or ("加密 Restic 仓库" if repository else ""),
            limit=120,
        ),
        "kind": _string(
            target_source.get("kind")
            or ("restic" if repository else ""),
            limit=60,
        ),
        "status": _string(
            target_source.get("status")
            or ("ready" if repository else ""),
            limit=60,
        ),
        "encrypted": _boolean(
            target_source.get("encrypted"),
            bool(repository),
        ),
    }
    for key in ("path", "mount_path", "root"):
        value = _string(target_source.get(key), limit=1200)
        if value:
            target[key] = value
    if repository:
        target["path"] = repository
    return target


def _configured_coverage(runtime_profiles: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One coverage entry per backup profile, from the profiles' sources."""
    coverage = []
    for profile in BACKUP_OPERATIONS:
        profile_config = _mapping(runtime_profiles.get(profile))
        sources = profile_config.get("sources")
        excludes = profile_config.get("excludes")
        source_records = (
            sources
            if isinstance(sources, list)
            else []
        )
        coverage.append({
            "source_id": profile,
            "label": (
                "关键状态数据域"
                if profile == "critical"
                else "完整 Resource 数据域"
            ),
            "description": _string(
                profile_config.get("description"),
                limit=240,
            ),
            "tier": profile,
            "required": any(
                _boolean(_mapping(item).get("required"), True)
                for item in source_records
            ),
            "path_count": len(source_records),
            "required_count": sum(
                1
                for item in source_records
                if _boolean(_mapping(item).get("required"), True)
            ),
            "excluded_count": (
                len(excludes)
                if isinstance(excludes, list)
                else 0
            ),
        })
    return coverage


def _policy(
    config: Mapping[str, Any],
    status: Mapping[str, Any],
    schedule: Mapping[str, Any],
    *,
    active_run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    config_backup = _mapping(config.get("backup"))
    configured = _mapping(config.get("policy") or config_backup.get("policy"))
    runtime = _mapping(status.get("policy"))
    default_profile = _string(
        runtime.get("default_profile")
        or configured.get("default_profile")
        or "critical",
        limit=40,
    ).casefold()
    if default_profile not in BACKUP_OPERATIONS:
        default_profile = "critical"
    timer_by_name = {
        item.get("operation"): item
        for item in schedule.get("timers") or []
        if isinstance(item, Mapping)
    }
    profiles = _profile_policies(configured, runtime, status, schedule, timer_by_name, active_run)
    return {
        "profiles": profiles,
        "default_profile": default_profile,
        "retention": _retention_policy(configured, runtime),
        "verification": _verification(runtime, status, timer_by_name, active_run),
    }


def _profile_policies(
    configured: Mapping[str, Any],
    runtime: Mapping[str, Any],
    status: Mapping[str, Any],
    schedule: Mapping[str, Any],
    timer_by_name: Mapping[str, Any],
    active_run: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Each backup profile: what it covers, its timer and its state."""
    runtime_profiles = runtime.get("profiles") or status.get("profiles")
    configured_profiles = configured.get("profiles")
    profiles = []
    action_by_name = {item.get("operation"): item for item in schedule.get("actions") or []}
    for profile in BACKUP_OPERATIONS:
        timer_status = timer_by_name.get(profile) or {}
        runtime_profile = _named_record(runtime_profiles, profile)
        configured_profile = _named_record(configured_profiles, profile)
        last_success_at = _timestamp(
            runtime_profile.get("last_success_at")
            or runtime_profile.get("latest_success_at")
        )
        profile_status = _health_status(runtime_profile.get("status"))
        if (
            active_run
            and active_run.get("kind") == "backup"
            and active_run.get("profile") == profile
        ):
            profile_status = "running"
        elif profile_status == "healthy":
            profile_status = "ready"
        elif profile_status == "unknown" and last_success_at:
            profile_status = "ready"
        elif profile_status == "unknown" and timer_status.get("available"):
            profile_status = "ready"
        elif profile_status == "unknown":
            profile_status = "setup_required"
        calendar = _string(timer_status.get("calendar"), limit=160)
        if timer_status.get("paused"):
            calendar += " · 已关闭"
        profiles.append({
            "id": profile,
            "name": profile,
            "label": BACKUP_LABELS[profile],
            "description": _string(
                configured_profile.get("description"),
                limit=240,
            ) or (
                "高频保护控制平面、会话与凭据元数据。"
                if profile == "critical"
                else "错峰保护完整 Resource 内容与可恢复工作区。"
            ),
            "source_count": _number(
                configured_profile.get("source_count")
            ),
            "required_source_count": _number(
                configured_profile.get("required_source_count")
            ),
            "exclude_count": _number(
                configured_profile.get("exclude_count")
            ),
            "calendar": calendar,
            "last_success_at": last_success_at,
            "next_run_at": _string(timer_status.get("next_trigger_at"), limit=120),
            "status": profile_status,
            "event_source_id": (action_by_name.get(profile) or {}).get("event_source_id", ""),
            "timer_event_id": timer_status.get("event_source_id", ""),
            "paused": bool(timer_status.get("paused")),
        })
    return profiles


def _retention_policy(configured: Mapping[str, Any], runtime: Mapping[str, Any]) -> dict[str, int]:
    retention_raw = _mapping(runtime.get("retention") or configured.get("retention"))
    return {
        "hourly": _number(
            retention_raw.get("hourly")
            or retention_raw.get("keep_hourly")
        ),
        "daily": _number(
            retention_raw.get("daily")
            or retention_raw.get("keep_daily")
        ),
        "weekly": _number(
            retention_raw.get("weekly")
            or retention_raw.get("keep_weekly")
        ),
        "monthly": _number(
            retention_raw.get("monthly")
            or retention_raw.get("keep_monthly")
        ),
    }


def _verification(
    runtime: Mapping[str, Any],
    status: Mapping[str, Any],
    timer_by_name: Mapping[str, Any],
    active_run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The verification modes, when each last passed, and their timers."""
    maintenance = _mapping(status.get("maintenance"))
    verification_raw = _mapping(
        runtime.get("verification")
        or status.get("verification")
    )
    check = _mapping(verification_raw.get("check") or maintenance.get("check"))
    deep_check = _mapping(
        verification_raw.get("deep_check")
        or verification_raw.get("deep-check")
        or maintenance.get("deep-check")
    )
    return {
        "modes": list(VERIFICATION_OPERATIONS),
        "last_check_at": _timestamp(
            verification_raw.get("last_check_at")
            or check.get("last_success_at")
        ),
        "last_deep_check_at": _timestamp(
            verification_raw.get("last_deep_check_at")
            or deep_check.get("last_success_at")
        ),
        "check": _verification_policy(
            "check",
            check,
            timer_by_name,
            active_run,
        ),
        "deep_check": _verification_policy(
            "deep-check",
            deep_check,
            timer_by_name,
            active_run,
        ),
    }


def _verification_policy(
    mode: str,
    runtime: Mapping[str, Any],
    timer_by_name: Mapping[str, Any],
    active_run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    timer_status = _mapping(timer_by_name.get(mode))
    status = _health_status(runtime.get("status"))
    if (
        active_run
        and active_run.get("kind") == "verification"
        and active_run.get("mode") == mode
    ):
        status = "running"
    elif status == "healthy":
        status = "ready"
    elif status == "unknown" and runtime.get("last_success_at"):
        status = "ready"
    elif status == "unknown" and timer_status.get("available"):
        status = "ready"
    elif status == "unknown":
        status = "setup_required"
    return {
        "mode": mode,
        "status": status,
        "last_success_at": _timestamp(runtime.get("last_success_at")),
        "last_attempt_at": _timestamp(runtime.get("last_attempt_at")),
        "next_run_at": _string(
            timer_status.get("next_trigger_at"),
            limit=120,
        ),
        "timer_event_id": timer_status.get("event_source_id", ""),
        "paused": bool(timer_status.get("paused")),
    }
