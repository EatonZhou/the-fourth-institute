from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

from management_common.database_time import iso_time

from . import application_targets, system_application_flows
from .notice_board_backend import NOTICE_BOARD_CATEGORIES, NoticeBoardBackend, notice_board
from .resident_handoff import ResidentInputTerminated, resume_and_wait
from .lineage_workflows import WorkflowBindings, WorkflowPending, WorkflowFailed
from .resources.registry_store import get_registry_store


# One registry document per post: its classification request and progress.
STATE_COLLECTION = "notice_board_classification"
# Classification is Portal maintenance, not a request made by a named User.
CLASSIFIER_ACTOR = "portal"
# The application whose configured Resident classifies posts (application_targets.py).
APPLICATION = "notice_classifier"
APPLICATION_TARGETS = {APPLICATION: {"target": "resident"}}
POLL_SECONDS = max(
    2.0,
    float(os.getenv("PORTAL_NOTICE_BOARD_CLASSIFIER_POLL_SECONDS", "5")),
)
MAX_ATTEMPTS = 5


def _now_iso() -> str:
    return iso_time(datetime.now(timezone.utc))


def _deliver_classification(text: str, *, actor: str, business_ref: str, idempotency_key: str) -> dict:
    return system_application_flows.submit(APPLICATION, actor=actor, entry="system",
                                           prompt=text, request_id=idempotency_key)


class NoticeBoardClassifier:
    """Durably dispatch and reconcile asynchronous Notice Board classification."""

    def __init__(
        self,
        board: NoticeBoardBackend,
        *,
        deliver: object | None = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.board = board
        self.deliver = deliver or _deliver_classification
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._thread_lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="portal-notice-board-classifier",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def enqueue(self, post_id: str) -> dict[str, Any]:
        source = self.board.classification_source(post_id)
        item = {
            "post_id": source["post_id"],
            "source_fingerprint": source["fingerprint"],
            "selected_category": source["category"],
            "status": "pending",
            "resident_id": "",
            "lineage_id": "",
            "message_id": "",
            "attempts": 0,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "last_error": "",
        }
        with self._thread_lock, get_registry_store().writer(STATE_COLLECTION) as writer:
            writer.put(STATE_COLLECTION, source["post_id"], item)
        self.start()
        self._wake.set()
        return dict(item)

    def run_once(self) -> bool:
        item = self._claim_or_observe_item()
        if not item:
            return False
        if item["status"] == "claiming":
            self._submit(item)
        elif item["status"] == "submitted":
            self._reconcile(item)
        elif item["status"] == "workflow_pending":
            self._observe_workflow(item)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            worked = False
            try:
                worked = self.run_once()
            except Exception as exc:
                print(f"Notice Board classifier pass failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                worked = False
            self._wake.wait(0.2 if worked else self.poll_seconds)
            self._wake.clear()

    def _claim_or_observe_item(self) -> dict[str, Any] | None:
        now = time.time()
        with self._thread_lock, get_registry_store().writer(STATE_COLLECTION) as writer:
            for item in self._ordered_items(writer.documents(STATE_COLLECTION)):
                status = str(item.get("status") or "pending")
                if status == "submitted" or (status == "workflow_pending" and float(item.get("retry_after") or 0) <= now):
                    return dict(item)
                if status == "claiming" and float(item.get("lease_until") or 0) > now:
                    continue
                if status in {"pending", "claiming"} and int(item.get("attempts") or 0) < MAX_ATTEMPTS:
                    if float(item.get("retry_after") or 0) > now:
                        continue
                    item["status"] = "claiming"
                    item["lease_until"] = now + 30.0
                    item["updated_at"] = _now_iso()
                    writer.put(STATE_COLLECTION, item["post_id"], item)
                    return dict(item)
        return None

    def _submit(self, item: dict[str, Any]) -> None:
        try:
            source = self.board.classification_source(item["post_id"])
            if source["fingerprint"] != item["source_fingerprint"]:
                self._finish(item["post_id"], "stale")
                return
            business_ref = f"notice-category:{source['post_id']}"
            receipt = self.deliver(
                self._prompt(source),
                actor=CLASSIFIER_ACTOR,
                business_ref=business_ref,
                idempotency_key=f"{business_ref}:{source['fingerprint']}:{int(item.get('attempts') or 0)}",
            )
            resident_id = str(receipt.get("resident_id") or "")
            handle = {
                "resident_id": resident_id,
                "lineage_id": str(receipt.get("lineage_id") or application_targets.resident(APPLICATION)["lineage_id"]) if resident_id else "",
                "message_id": str(receipt.get("message_id") or ""),
            }
            self._update_item(
                item["post_id"],
                status="submitted",
                resident_id=str(handle.get("resident_id") or ""),
                lineage_id=str(handle.get("lineage_id") or ""),
                message_id=str(handle.get("message_id") or ""),
                event_id=str(receipt.get("event_id") or ""),
                trigger_path_id=str(receipt.get("trigger_path_id") or ""),
                lease_until=0,
                last_error="",
            )
        except system_application_flows.ApplicationFlowUnavailable as exc:
            # Closing a graph component retains the durable request without
            # spending its model-attempt budget. Reopening resumes this item.
            self._update_item(item["post_id"], status="pending", lease_until=0,
                              retry_after=time.time() + self.poll_seconds, last_error=str(exc)[:1000])
        except WorkflowPending as exc:
            self._update_item(item["post_id"], status="workflow_pending", workflow_handle=exc.handle,
                              lease_until=0, retry_after=time.time() + self.poll_seconds, last_error=str(exc))
        except WorkflowFailed as exc:
            self._update_item(item["post_id"], status="failed", workflow_handle=exc.handle, lease_until=0, last_error=str(exc))
        except Exception as exc:
            self._retry(item["post_id"], exc)

    def _observe_workflow(self, item: dict[str, Any]) -> None:
        try:
            receipt = WorkflowBindings().observe(item["workflow_handle"], actor=CLASSIFIER_ACTOR, entry="system")
        except WorkflowFailed as exc:
            self._update_item(item["post_id"], status="failed", lease_until=0, last_error=str(exc))
            return
        except Exception as exc:
            # This Event already exists. Transport errors and paused Effects
            # must never be interpreted as permission to create another one.
            self._update_item(item["post_id"], retry_after=time.time() + self.poll_seconds, last_error=str(exc)[:1000])
            return
        self._update_item(item["post_id"], status="submitted", lease_until=0, last_error="",
                          **{key: receipt[key] for key in ("resident_id", "lineage_id", "message_id", "event_id", "trigger_path_id")})

    def _reconcile(self, item: dict[str, Any]) -> None:
        try:
            result = resume_and_wait(
                resident_id=item["resident_id"],
                actor=CLASSIFIER_ACTOR,
                lineage_id=str(item.get("lineage_id") or ""),
                message_id=item["message_id"],
                timeout_seconds=self.poll_seconds,
            )
        except ResidentInputTerminated as exc:
            self._update_item(item["post_id"], status=exc.status, last_error=str(exc), lease_until=0)
            return
        except Exception as exc:
            # Keep observing the same durable execution after transient Portal
            # execution-service failures instead of creating a duplicate call.
            self._update_item(item["post_id"], last_error=str(exc)[:1000])
            return
        try:
            category = self._parse_category(result.get("message"))
            self.board.apply_automatic_category(
                item["post_id"],
                expected_fingerprint=item["source_fingerprint"],
                category=category,
            )
            self._finish(item["post_id"], "completed", category=category)
        except Exception as exc:
            self._retry(item["post_id"], exc)

    @staticmethod
    def _prompt(source: dict[str, str]) -> str:
        categories = "\n".join(
            f"- {item['id']}: {item['label']}" for item in NOTICE_BOARD_CATEGORIES
        )
        return (
            "你是 TFI Portal Notice Board 的分类 Agent。根据标题和正文选择唯一类别。\n"
            "分类标准：Bug 是明确故障；需求是功能请求、构想或建议；release 是 Portal 发布、更新、维护或变更公告；chat 是生活、闲谈或非工作讨论；无法明确判断用 other。\n"
            f"可选类别：\n{categories}\n"
            f"用户当前选择：{source['category']}\n"
            f"标题：{source['title']}\n"
            f"正文：{source['content']}\n"
            "只返回 JSON：{\"category\":\"类别 id\"}"
        )

    @staticmethod
    def _parse_category(value: object) -> str:
        text = str(value or "").strip()
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                payload = json.loads(match.group(0))
                category = str(payload.get("category") or "").strip().casefold()
            except (TypeError, ValueError, json.JSONDecodeError):
                category = ""
        else:
            category = text.strip('`" ').casefold()
        valid = {item["id"] for item in NOTICE_BOARD_CATEGORIES}
        if category not in valid:
            raise ValueError("classifier returned an invalid category")
        return category

    def _retry(self, post_id: str, exc: BaseException) -> None:
        with self._thread_lock, get_registry_store().writer(STATE_COLLECTION) as writer:
            item = writer.get(STATE_COLLECTION, post_id)
            if item is None:
                return
            attempts = int(item.get("attempts") or 0) + 1
            item.update({
                "attempts": attempts,
                "status": "failed" if attempts >= MAX_ATTEMPTS else "pending",
                "resident_id": "",
                "lineage_id": "",
                "message_id": "",
                "lease_until": 0,
                "retry_after": time.time() + min(300.0, 5.0 * (2 ** (attempts - 1))),
                "updated_at": _now_iso(),
                "last_error": str(exc)[:1000],
            })
            writer.put(STATE_COLLECTION, post_id, item)

    def _finish(self, post_id: str, status: str, *, category: str = "") -> None:
        self._update_item(
            post_id,
            status=status,
            category=category,
            resident_id="",
            lineage_id="",
            message_id="",
            lease_until=0,
            updated_at=_now_iso(),
        )

    def _update_item(self, post_id: str, **updates: Any) -> None:
        with self._thread_lock, get_registry_store().writer(STATE_COLLECTION) as writer:
            item = writer.get(STATE_COLLECTION, post_id)
            if item is None:
                return
            item.update(updates)
            item["updated_at"] = _now_iso()
            writer.put(STATE_COLLECTION, post_id, item)

    def items(self) -> dict[str, dict[str, Any]]:
        """Every post's classification request, keyed by post ID."""
        return get_registry_store().documents(STATE_COLLECTION)

    @staticmethod
    def _ordered_items(documents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Requests in the order they were made."""
        return sorted(
            (dict(item, post_id=post_id) for post_id, item in documents.items()),
            key=lambda item: (str(item.get("created_at") or ""), item["post_id"]),
        )


# The Web process classifies the posts created through its ISA handlers.
CLASSIFIER = NoticeBoardClassifier(notice_board)


def enqueue_classification(post_id: object) -> None:
    """Queue a new post; a failure leaves it unclassified and does not fail the post."""
    try:
        CLASSIFIER.enqueue(str(post_id or ""))
    except Exception as exc:
        print(f"Notice Board classifier enqueue failed: {exc}", file=sys.stderr)


def web_lifespan():
    """Classify posts while Web runs (``web.lifespan``)."""
    CLASSIFIER.start()
    return CLASSIFIER.stop
