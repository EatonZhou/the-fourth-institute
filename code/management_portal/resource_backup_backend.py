from __future__ import annotations
from management_common.product_environment import read_product_env
from management_common.portal_paths import path as portal_path


import json
import logging
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from management_common.operation_errors import OperationError
from management_portal.backup_lineage import (
    BACKUP_OPERATIONS, VERIFICATION_OPERATIONS, BackupLineage, lineage_id, restore_guide,
)

from management_portal.resource_backup_policy import _configuration_projection, _policy
from management_portal.resource_backup_runs import _active_run, _recent_runs
from management_portal.resource_backup_values import (
    _mapping, _string, _number, _boolean, _safe_identifier, _timestamp, _run_status, _health_status,
    _coverage_status, _named_record,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_SERVICE_ROOT = (portal_path("share_root") / "Services" / "resource-backup")
SERVICE_ROOT_ENV = "PORTAL_RESOURCE_BACKUP_ROOT"

_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class ResourceBackupError(OperationError):
    """Base class for Resource Backup adapter failures."""


class ResourceBackupConflict(ResourceBackupError):
    """A backup/check operation is already active."""

    code = "resource_backup.conflict"
    http_status = 409
    commit_state = "not_committed"


class ResourceBackupUnavailable(ResourceBackupError):
    """The fixed Resource Backup executor cannot accept an operation."""

    code = "resource_backup.unavailable"
    http_status = 503


DiskUsage = Callable[[str | os.PathLike[str]], Any]
Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class ResourceBackupBackend:
    """Read the backup executor projection and trigger fixed Lineage events.

    This adapter deliberately does not execute restic or interpret secret
    references. Lineage schedules work; the backup service owns execution.
    """

    def __init__(
        self,
        service_root: str | Path | None = None,
        *,
        workflow: BackupLineage | None = None,
        disk_usage: DiskUsage | None = None,
        clock: Clock | None = None,
    ):
        configured_root = (
            Path(service_root)
            if service_root is not None
            else Path(read_product_env(SERVICE_ROOT_ENV, str(DEFAULT_SERVICE_ROOT)))
        )
        self.service_root = configured_root.expanduser().resolve(strict=False)
        self.config_path = self.service_root / "config" / "config.yaml"
        self.status_path = self.service_root / "state" / "status.json"
        self.runs_root = self.service_root / "state" / "runs"
        self._workflow = workflow or BackupLineage()
        self._disk_usage = disk_usage or shutil.disk_usage
        self._clock = clock or _default_clock

    def get_overview(self, actor: str) -> dict[str, Any]:
        runtime_config, config_state = self._load_yaml(self.config_path)
        config = _configuration_projection(runtime_config)
        status, status_state = self._load_status()
        run_records = self._load_run_records()
        active_run = _active_run(status, run_records)
        recent_runs = _recent_runs(status, run_records, active_run)
        schedule = self._schedule_status(actor)
        target = self._target(config, status)
        coverage = self._coverage(config, status)
        health = self._health(
            status,
            status_state=status_state,
            config_state=config_state,
            active_run=active_run,
        )
        return {
            "schema": "tfi.resource-backup-overview.v1",
            "generated_at": self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "health": health,
            "policy": _policy(
                config,
                status,
                schedule,
                active_run=active_run,
            ),
            "target": target,
            "coverage": coverage,
            "active_run": active_run,
            "recent_runs": recent_runs,
            "permissions": self._permissions(actor),
            "schedule": schedule,
            "warnings": self._warnings(
                health=health,
                target=target,
                coverage=coverage,
                config_state=config_state,
                status_state=status_state,
                schedule=schedule,
                status=status,
            ),
            "restore_guide": restore_guide(),
        }

    def request_backup(self, profile: str, actor: str) -> dict[str, Any]:
        normalized = _string(profile, limit=40).casefold()
        if normalized not in BACKUP_OPERATIONS:
            raise ValueError("profile must be critical or content")
        self._ensure_idle()
        receipt = self._start(normalized, actor)
        return {
            "accepted": True,
            "kind": "backup",
            "profile": normalized,
            "status": "queued",
            **receipt,
        }

    def request_verification(self, mode: str, actor: str) -> dict[str, Any]:
        normalized = _string(mode, limit=40).casefold()
        if normalized not in VERIFICATION_OPERATIONS:
            raise ValueError("mode must be check or deep-check")
        self._ensure_idle()
        receipt = self._start(normalized, actor)
        return {
            "accepted": True,
            "kind": "verification",
            "mode": normalized,
            "status": "queued",
            **receipt,
        }

    def _permissions(self, actor: str) -> dict[str, bool]:
        """Buttons ask the instructions they call: TriggerEvent on the backup Lineage, CancelScriptRun for its runs."""
        from management_portal import permission_system

        actor_id = _string(actor, limit=160)
        target = {"lineage_id": lineage_id(required=False)}
        configured = bool(target["lineage_id"])
        run = configured and permission_system.allows(actor_id, "TriggerEvent", target)
        return {
            "can_view": bool(actor_id),
            "can_run": run,
            "can_verify": run,
            "can_cancel": configured and permission_system.allows(actor_id, "CancelScriptRun", target),
        }

    def _ensure_idle(self) -> None:
        status, _state = self._load_status()
        state = _run_status(status.get("state") or status.get("status"))
        if state in {"queued", "running"}:
            raise ResourceBackupConflict(
                "A Resource Backup operation is already active"
            )
        active = _active_run(status, self._load_run_records())
        if active:
            raise ResourceBackupConflict("A Resource Backup operation is already active")

    def _schedule_status(self, actor: str) -> dict[str, Any]:
        try:
            return self._workflow.schedule(actor)
        except Exception:  # noqa: BLE001 - the overview reports an unreadable schedule
            # The page says only that the schedule is unreadable; the log says why.
            LOGGER.warning("Resource Backup schedule unreadable for %s", actor, exc_info=True)
            return {"readable": False, "lineage_id": "", "actions": [], "timers": []}

    def _start(self, operation: str, actor: str) -> dict[str, str]:
        """Use the same manual Event as the graph; never bypass its switches."""

        schedule = self._schedule_status(actor)
        actions = {item["operation"]: item for item in schedule["actions"]}
        action = actions.get(operation, {})
        if not action.get("available"):
            raise ResourceBackupUnavailable("备份流程未配置或已关闭，请在 Portal System 中打开对应 Event 和 Effect")
        if any(item.get("active") for item in actions.values()):
            raise ResourceBackupConflict("A Resource Backup operation is already active")
        from management_portal.hatg.contracts import InstructionError

        try:
            receipt = self._workflow.trigger(action["event_source_id"], actor)
        except InstructionError as exc:
            # TriggerEvent decides who may run a backup; its refusal reaches the page as such.
            if exc.code in {"instruction.forbidden", "instruction.not_found"}:
                raise PermissionError("没有在资源备份 Lineage 上触发这个 Event 的权限") from exc
            raise ResourceBackupUnavailable("Resource Backup executor is unavailable") from exc
        except Exception as exc:  # noqa: BLE001 - executor details stay server-side
            raise ResourceBackupUnavailable("Resource Backup executor is unavailable") from exc
        delivery = receipt.get("delivery") or {}
        accepted = any(item.get("status") == "executed" and item.get("target_id") == action.get("effect_id")
                       for item in delivery.get("results") or [])
        if not receipt.get("event_id") or not accepted:
            raise ResourceBackupUnavailable("备份流程没有接受本次操作，请检查 Event、Effect 与连线状态")
        return {"lineage_id": schedule.get("lineage_id", ""), "event_source_id": action["event_source_id"],
                "event_id": receipt["event_id"], "trigger_path_id": receipt.get("trigger_path_id", "")}

    def _health(
        self,
        status: Mapping[str, Any],
        *,
        status_state: str,
        config_state: str,
        active_run: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if status_state == "missing":
            return {
                "status": "setup_required",
                "label": self._health_label("setup_required"),
                "summary": "Backup status has not been initialized.",
                "message": "Backup status has not been initialized.",
                "latest_success_at": "",
                "last_success_at": "",
                "last_attempt_at": "",
                "last_verified_at": "",
                "recovery_point_age_seconds": 0,
                "consecutive_failures": 0,
                "warning_count": 0,
            }
        if status_state != "ok":
            return {
                "status": "unavailable",
                "label": self._health_label("unavailable"),
                "summary": "Backup status is unavailable.",
                "message": "Backup status is unavailable.",
                "latest_success_at": "",
                "last_success_at": "",
                "last_attempt_at": "",
                "last_verified_at": "",
                "recovery_point_age_seconds": 0,
                "consecutive_failures": 0,
                "warning_count": 0,
            }
        raw = _mapping(status.get("health"))
        raw_status = (
            raw.get("status")
            or status.get("health_status")
            or status.get("status")
        )
        normalized = _health_status(raw_status)
        profiles = _mapping(status.get("profiles"))
        profile_successes = [
            _timestamp(_mapping(value).get("last_success_at"))
            for value in profiles.values()
        ]
        profile_successes = [value for value in profile_successes if value]
        last_success = _timestamp(raw.get("last_success_at")) or (
            max(profile_successes)
            if profile_successes
            else _timestamp(
                status.get("last_success_at")
                or status.get("latest_success_at")
            )
        )
        maintenance = _mapping(status.get("maintenance"))
        verified_at_values = [
            _timestamp(_mapping(maintenance.get(mode)).get("last_success_at"))
            for mode in ("check", "deep-check")
        ]
        verified_at_values = [value for value in verified_at_values if value]
        last_verified_at = _timestamp(
            raw.get("last_verified_at")
            or status.get("last_verified_at")
            or status.get("latest_verified_at")
        ) or (max(verified_at_values) if verified_at_values else "")
        last_error = _timestamp(
            raw.get("last_error_at")
            or status.get("last_error_at")
        )
        agent_state = _string(status.get("state"), limit=40).casefold()
        if active_run or agent_state in {"running", "backing_up", "verifying"}:
            normalized = "running"
        elif normalized == "unknown" and last_error and (
            not last_success or last_error > last_success
        ):
            normalized = "failed"
        elif normalized == "unknown" and last_success:
            normalized = "healthy"
        elif normalized == "unknown" and (
            config_state == "missing" or not last_success
        ):
            normalized = "setup_required"
        recovery_age = _number(
            raw.get("recovery_point_age_seconds")
            or status.get("recovery_point_age_seconds")
        )
        if not recovery_age and last_success:
            try:
                success_at = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
                recovery_age = max(
                    0,
                    int((self._clock().astimezone(timezone.utc) - success_at).total_seconds()),
                )
            except ValueError:
                recovery_age = 0
        return {
            "status": normalized,
            "label": self._health_label(normalized),
            "summary": self._health_message(normalized),
            "message": self._health_message(normalized),
            "latest_success_at": last_success,
            "last_success_at": last_success,
            "last_attempt_at": _timestamp(
                raw.get("last_attempt_at")
                or status.get("last_attempt_at")
            ),
            "last_verified_at": last_verified_at,
            "recovery_point_age_seconds": recovery_age,
            "consecutive_failures": _number(
                raw.get("consecutive_failures")
                or status.get("consecutive_failures")
            ),
            "warning_count": _number(
                raw.get("warning_count")
                or status.get("warning_count")
            ),
        }

    @staticmethod
    def _health_label(status: str) -> str:
        return {
            "healthy": "备份健康",
            "degraded": "备份需关注",
            "failed": "最近备份失败",
            "running": "正在生成恢复点",
            "setup_required": "待完成初始化",
            "unavailable": "状态暂不可用",
            "unknown": "状态尚未上报",
        }[status]

    @staticmethod
    def _health_message(status: str) -> str:
        return {
            "healthy": "关键状态和 Resource 内容都有可用恢复点。",
            "degraded": "部分恢复点需要处理，请检查下方告警和最近运行。",
            "failed": "最近一次 Resource 备份未完成，请查看运行详情。",
            "running": "后台正在生成新的加密恢复点，页面可以继续正常使用。",
            "setup_required": "完成首次备份后，这里会持续显示恢复点健康状态。",
            "unavailable": "当前无法读取备份服务状态。",
            "unknown": "备份服务尚未上报健康状态。",
        }[status]

    def _target(
        self,
        config: Mapping[str, Any],
        status: Mapping[str, Any],
    ) -> dict[str, Any]:
        config_backup = _mapping(config.get("backup"))
        configured = _mapping(config.get("target") or config_backup.get("target"))
        runtime = _mapping(status.get("target"))
        target_path = self._target_path(configured, runtime)
        disk = self._disk_space(target_path)
        raw_status = runtime.get("status") or configured.get("status")
        target_status = _health_status(raw_status)
        latest_success_at = _timestamp(
            status.get("last_success_at")
            or status.get("latest_success_at")
        )
        if target_status == "healthy":
            target_status = "ready"
        elif target_status in {"failed", "unavailable"}:
            target_status = "unavailable"
        elif target_status in {"unknown", "setup_required"} and latest_success_at and target_path:
            target_status = "ready"
        elif not configured and not runtime:
            target_status = "setup_required"
        elif target_path:
            target_status = "setup_required"
        else:
            target_status = "unknown"
        return {
            "label": _string(
                runtime.get("label")
                or configured.get("label")
                or "Resource backup target",
                limit=120,
            ),
            "kind": _string(
                runtime.get("kind")
                or configured.get("kind")
                or "unknown",
                limit=60,
            ),
            "status": target_status,
            "encrypted": _boolean(
                runtime.get("encrypted"),
                _boolean(configured.get("encrypted")),
            ),
            "repository_size_bytes": _number(
                runtime.get("repository_size_bytes")
                or runtime.get("size_bytes")
            ) or self._directory_size(target_path),
            **disk,
        }

    @staticmethod
    def _target_path(
        configured: Mapping[str, Any],
        runtime: Mapping[str, Any],
    ) -> str:
        for source in (configured, runtime):
            for key in ("path", "mount_path", "root"):
                value = _string(source.get(key), limit=1200)
                if value and Path(value).is_absolute():
                    return value
        return ""

    def _disk_space(self, path: str) -> dict[str, int]:
        if not path:
            return {
                "total_bytes": 0,
                "used_bytes": 0,
                "free_bytes": 0,
            }
        try:
            usage = self._disk_usage(path)
            total_raw = getattr(usage, "total", None)
            used_raw = getattr(usage, "used", None)
            free_raw = getattr(usage, "free", None)
            total = _number(total_raw if total_raw is not None else usage[0])
            used = _number(used_raw if used_raw is not None else usage[1])
            free = _number(free_raw if free_raw is not None else usage[2])
        except (IndexError, OSError, TypeError):
            return {
                "total_bytes": 0,
                "used_bytes": 0,
                "free_bytes": 0,
            }
        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
        }

    @staticmethod
    def _directory_size(path: str) -> int:
        if not path:
            return 0
        total = 0
        stack = [path]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        return total

    def _coverage(
        self,
        config: Mapping[str, Any],
        status: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        config_backup = _mapping(config.get("backup"))
        raw = (
            status.get("coverage")
            or config.get("coverage")
            or config_backup.get("coverage")
            or []
        )
        records: list[dict[str, Any]] = []
        if isinstance(raw, Mapping):
            for source_id, value in raw.items():
                item = _mapping(value)
                if not item and value is not None:
                    item["status"] = value
                item.setdefault("source_id", source_id)
                records.append(item)
        elif isinstance(raw, list):
            records = [_mapping(item) for item in raw if isinstance(item, Mapping)]
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        status_profiles = status.get("profiles")
        for item in records[:200]:
            source_id = _safe_identifier(
                item.get("source_id")
                or item.get("id")
                or item.get("provider_id")
            )
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            raw_types = item.get("resource_types")
            resource_types = [
                _safe_identifier(value)
                for value in (raw_types if isinstance(raw_types, list) else [])
            ]
            raw_paths = item.get("paths")
            path_count = _number(item.get("path_count"))
            if not path_count and isinstance(raw_paths, list):
                path_count = len(raw_paths)
            latest_snapshot_at = _timestamp(
                item.get("latest_snapshot_at")
                or item.get("last_snapshot_at")
                or item.get("updated_at")
            )
            profile_id = _string(
                item.get("tier")
                or item.get("profile")
                or source_id,
                limit=60,
            ).casefold()
            profile_state = _named_record(
                status_profiles,
                profile_id,
            )
            if not latest_snapshot_at:
                latest_snapshot_at = _timestamp(
                    profile_state.get("last_success_at")
                )
            coverage_status = _coverage_status(item.get("status"))
            if coverage_status in {"unknown", "setup_required"} and latest_snapshot_at:
                coverage_status = "protected"
            elif coverage_status == "unknown":
                coverage_status = "setup_required"
            normalized.append({
                "source_id": source_id,
                "label": _string(item.get("label") or source_id, limit=120),
                "description": _string(item.get("description"), limit=240),
                "tier": _string(item.get("tier") or item.get("profile"), limit=60),
                "required": _boolean(item.get("required")),
                "sensitive": _boolean(item.get("sensitive")),
                "status": coverage_status,
                "bytes": _number(item.get("bytes") or item.get("size_bytes")),
                "item_count": _number(item.get("item_count") or item.get("count")),
                "path_count": path_count,
                "required_count": _number(item.get("required_count")),
                "excluded_count": _number(item.get("excluded_count")),
                "warning_count": _number(item.get("warning_count")),
                "latest_snapshot_at": latest_snapshot_at,
                "last_snapshot_at": latest_snapshot_at,
                "resource_types": [value for value in resource_types if value],
            })
        return normalized

    @staticmethod
    def _warnings(
        *,
        health: Mapping[str, Any],
        target: Mapping[str, Any],
        coverage: Sequence[Mapping[str, Any]],
        config_state: str,
        status_state: str,
        schedule: Mapping[str, Any],
        status: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        if config_state == "invalid":
            warnings.append({
                "code": "agent_projection_invalid",
                "title": "备份服务配置无效",
                "message": "请检查已配置备份服务目录中的 config/config.yaml。",
            })
        if status_state == "invalid":
            warnings.append({
                "code": "status_projection_invalid",
                "title": "备份状态暂不可读",
                "message": "备份服务的状态文件需要修复。",
            })
        elif status_state == "missing":
            warnings.append({
                "code": "setup_required",
                "title": "备份服务尚未初始化",
                "message": "完成首次配置与运行后会生成状态投影。",
            })
        if target.get("status") == "unavailable":
            warnings.append({
                "code": "target_unavailable",
                "title": "备份目标不可用",
                "message": "后台执行器当前无法访问备份目标。",
            })
        if _string(target.get("kind"), limit=60).casefold().startswith("local"):
            warnings.append({
                "code": "no_offsite_replica",
                "title": "尚无异地副本",
                "message": "/data 可用于快速恢复本机数据，但不能覆盖整机或磁盘同时损坏。",
            })
        if any(item.get("status") == "excluded" for item in coverage):
            warnings.append({
                "code": "large_assets_excluded",
                "title": "大模型与数据集未纳入",
                "message": "它们已位于 /data；同盘复制不构成备份，需要另一块磁盘或对象存储。",
            })
        unavailable_sources = [
            item
            for group in ("actions", "timers")
            for item in schedule.get(group) or []
            if isinstance(item, Mapping) and not item.get("available")
        ]
        if unavailable_sources or not schedule.get("readable", False):
            warnings.append({
                "code": "scheduler_unavailable",
                "title": "自动备份计划尚未就绪",
                "message": "Portal System 中的备份 Event、Effect 或连线缺失、已关闭，或当前无法读取。",
            })
        raw_warnings = status.get("warnings")
        if isinstance(raw_warnings, list):
            for value in raw_warnings[:20]:
                item = _mapping(value)
                code = _safe_identifier(item.get("code")) if item else ""
                warnings.append({
                    "code": code or "agent_warning",
                    "title": "备份服务报告告警",
                    "message": "请在 Portal System 的备份流程中查看 ScriptRun 日志和备份运行记录。",
                })
        if health.get("status") == "failed" and not any(
            item["code"] == "latest_run_failed"
            for item in warnings
        ):
            warnings.append({
                "code": "latest_run_failed",
                "title": "最近一次备份未完成",
                "message": "可以在排除后台错误后重新发起对应备份。",
            })
        return warnings[:24]

    def _load_run_records(self) -> list[dict[str, Any]]:
        candidates: list[Path] = []
        for root in (self.runs_root,):
            if not root.is_dir():
                continue
            try:
                children = list(root.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_symlink():
                    continue
                if child.is_file() and child.suffix.lower() == ".json":
                    candidates.append(child)
                elif child.is_dir():
                    result_path = child / "result.json"
                    if result_path.is_file() and not result_path.is_symlink():
                        candidates.append(result_path)
        records: list[dict[str, Any]] = []
        try:
            paths = sorted(
                candidates,
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        for path in paths[:100]:
            payload, state = self._load_json(path)
            if state == "ok":
                records.append(payload)
        return records

    def _load_status(self) -> tuple[dict[str, Any], str]:
        return self._load_json(self.status_path)

    @staticmethod
    def _load_json(path: Path) -> tuple[dict[str, Any], str]:
        if not path.is_file():
            return {}, "missing"
        try:
            if path.stat().st_size > _MAX_DOCUMENT_BYTES:
                return {}, "invalid"
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}, "invalid"
        return (_mapping(payload), "ok") if isinstance(payload, Mapping) else ({}, "invalid")

    @staticmethod
    def _load_yaml(path: Path) -> tuple[dict[str, Any], str]:
        if not path.is_file():
            return {}, "missing"
        try:
            if path.stat().st_size > _MAX_DOCUMENT_BYTES:
                return {}, "invalid"
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return {}, "invalid"
        return (_mapping(payload), "ok") if isinstance(payload, Mapping) else ({}, "invalid")


resource_backup_backend = ResourceBackupBackend()


def get_overview(actor: str) -> dict[str, Any]:
    return resource_backup_backend.get_overview(actor)


def request_backup(profile: str, actor: str) -> dict[str, Any]:
    return resource_backup_backend.request_backup(profile, actor)


def request_verification(mode: str, actor: str) -> dict[str, Any]:
    return resource_backup_backend.request_verification(mode, actor)
