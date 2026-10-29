"""Resident discovery: catalog, summary jobs, embeddings and search.

Mechanical work (catalog sync, change detection, evidence preparation,
embeddings) runs in SocietyService's worker.  Summaries are written by the
Resident holding the Portal duty ``resident-summary``, through
``claim_summary_jobs`` (which hands out the evidence) → ``submit_summary`` (or
``release_summary_job``), with that Resident's own model and account.
"""
from __future__ import annotations

import hashlib
import gc
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from management_common.json_columns import json_value
from management_common.embedding_feature import set_enabled as set_embeddings_enabled

from .embedder import (
    EMBEDDING_RUNTIME_LOCK, ResidentEmbedder, resident_embeddings_available,
    shared_embedder, unload_shared_embedder,
)
from .evidence import (
    MAX_SUMMARY_RESIDENTS,
    PROMPT_VERSION,
    SUMMARY_FIELDS,
    SUMMARY_INSTRUCTIONS,
    fingerprint,
    message_fingerprint,
    message_id,
    policy,
    project_messages_for_summary,
    project_summary_evidence,
    project_summary_metadata,
    redact_text,
    summary_text,
    summary_with_pending_requests,
    utc_now,
    validate_summary,
)
from .source import LineageResidentSource
from .store import ResidentContextStore, after

CONVERSATION_REASONS = {"context_cursor_advanced", "conversation_messages_changed"}


@dataclass(frozen=True)
class Settings:
    catalog_sync_seconds: float = 300.0
    reconcile_seconds: float = 30.0
    reconcile_batch_size: int = 8
    debounce_seconds: float = 120.0
    full_rebuild_incrementals: int = 20
    full_rebuild_max_age_days: int = 30
    max_job_attempts: int = 6
    prepare_batch_size: int = 4
    prepare_lease_seconds: float = 1800.0
    claim_lease_seconds: float = 5400.0
    evidence_budget_chars: int = 160_000
    search_pool: int = 60


class ResidentContext:
    def __init__(
        self,
        *,
        store: ResidentContextStore | None = None,
        source: LineageResidentSource | None = None,
        embedder: ResidentEmbedder | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store or ResidentContextStore()
        self.source = source or LineageResidentSource()
        self._embedder = embedder
        self._explicit_embedder = embedder is not None
        self.settings = settings or Settings()

    @property
    def embedder(self) -> ResidentEmbedder:
        if self._embedder is None:
            self._embedder = shared_embedder()
        return self._embedder

    @property
    def embeddings_available(self) -> bool:
        # A previously loaded shared model must not bypass a switch turned off at runtime.
        # Explicitly injected embedders are used by isolated callers and tests.
        return self._explicit_embedder or resident_embeddings_available()

    def set_embeddings_enabled(self, value: bool) -> dict[str, bool]:
        """Apply the switch in the process that owns the model and vector jobs."""
        if not isinstance(value, bool):
            raise ValueError("enabled must be a boolean")
        with EMBEDDING_RUNTIME_LOCK:
            set_embeddings_enabled(value)
            if not value:
                if not self._explicit_embedder:
                    self._embedder = None
                unload_shared_embedder()
                gc.collect()
            return {"enabled": value}

    # -- catalog and change detection -----------------------------------------

    def sync_catalog(self) -> dict[str, Any]:
        """Mirror the catalog; forget Residents whose removal is recorded.

        A Resident missing from the catalog turns inactive and leaves search and
        recommendations. A deleted Resident, one in a deleted Lineage or one no
        longer on record loses its summary and vector at once; Portal keeps no
        archive. One only missing because its Lineage failed to read keeps
        them until it comes back.
        """

        records = self.source.discover()
        if not records and self.store.counts()["residents"]:
            raise RuntimeError("Lineage discovery returned an empty catalog; preserving the last known catalog")
        result = self.store.upsert_catalog(records)
        inactive = self.store.inactive_resident_ids()
        removed = self.source.removed_resident_ids(inactive) if inactive else set()
        purged = self.store.purge(sorted(removed))
        return {"schema": "tfi.resident-context.catalog-sync.v1", **result, "purged": purged}

    def reconcile(self, *, limit: int | None = None) -> dict[str, Any]:
        resident_ids = self.store.residents_due_for_check(limit or self.settings.reconcile_batch_size)
        changed = errors = 0
        for resident_id in resident_ids:
            try:
                changed += self._reconcile_one(resident_id)
            except Exception:  # noqa: BLE001 - one Resident's failure never blocks the rest
                errors += 1
            self.store.mark_checked(resident_id)
        return {
            "schema": "tfi.resident-context.reconcile.v1",
            "checked": len(resident_ids), "changed": changed, "errors": errors,
        }

    def _global_summary_managed(self, resident_id: str) -> bool:
        current = self.store.resident(resident_id) or {}
        if not current.get("global_summary_managed", True):
            return False
        if hasattr(self.source, "global_summary_resident_ids"):
            return resident_id in self.source.global_summary_resident_ids([resident_id])
        return True

    def _reconcile_one(self, resident_id: str) -> int:
        if not self._global_summary_managed(resident_id):
            self.store.discard_summary_job(resident_id)
            return 0
        snapshot = self.source.conversation_snapshot(resident_id)
        current = self.store.resident(resident_id) or {}
        messages = list(snapshot.get("messages") or [])
        current_fingerprint = message_fingerprint(messages)
        current_ids = [message_id(item) for item in messages if message_id(item)]
        stored_fingerprint = str(current.get("message_fingerprint") or "")
        generation = int(current.get("generation") or 0)
        if generation and not stored_fingerprint and current_ids == [str(v) for v in current.get("message_ids") or []]:
            self.store.set_message_fingerprint_baseline(resident_id, current_fingerprint)
            stored_fingerprint = current_fingerprint
        job = self.store.summary_job(resident_id) or {}
        status = str(job.get("status") or "")
        revive = False
        if status == "failed":
            # A parked job runs again only after its inputs change.
            source_key = self._failure_source_key(current, current_fingerprint)
            failed_source = str(job.get("failed_source") or "")
            if not failed_source:
                self.store.set_failed_source(resident_id, source_key)
            if failed_source in {"", source_key}:
                return 0
            revive = True
        if status in {"ready", "claimed"} and self._prepared_fingerprint(job) == current_fingerprint:
            return 0  # The prepared evidence is still the current conversation.
        enqueue = self.store.enqueue
        changed = 0
        if not generation:
            enqueue(resident_id, mode="full", reason="missing_summary", available_at=utc_now(), revive_failed=revive)
            changed = 1
        elif str(current.get("prompt_version") or "") != PROMPT_VERSION:
            enqueue(resident_id, mode="full", reason="summary_projection_changed", available_at=utc_now(),
                    revive_failed=revive)
            changed = 1
        elif int(snapshot.get("cursor") or 0) > int(current.get("source_cursor") or 0):
            enqueue(resident_id, mode="incremental", reason="context_cursor_advanced",
                    available_at=after(self.settings.debounce_seconds), revive_failed=revive)
            changed = 1
        elif current_fingerprint != stored_fingerprint:
            enqueue(resident_id, mode="incremental", reason="conversation_messages_changed",
                    available_at=after(self.settings.debounce_seconds), revive_failed=revive)
            changed = 1
        if int(current.get("incremental_generations") or 0) >= self.settings.full_rebuild_incrementals:
            enqueue(resident_id, mode="full", reason="incremental_generation_threshold", available_at=utc_now(),
                    revive_failed=revive)
        # Only incremental summaries drift; with none since the last full one
        # a rebuild would rewrite the same text from the same evidence.
        if (
            generation and int(current.get("incremental_generations") or 0)
            and _older_than(str(current.get("rebuilt_at") or ""), days=self.settings.full_rebuild_max_age_days)
        ):
            enqueue(resident_id, mode="full", reason="periodic_full_rebuild", available_at=utc_now(),
                    revive_failed=revive)
        return changed

    @staticmethod
    def _prepared_fingerprint(job: dict[str, Any]) -> str:
        try:
            evidence = json_value(job.get("evidence_json") or "{}")
        except ValueError:
            return ""
        return str((evidence.get("source") or {}).get("message_fingerprint") or "")

    def _failure_source_key(self, current: dict[str, Any], current_fingerprint: str) -> str:
        """Everything a summary depends on; a parked job reruns only when it changes."""

        return fingerprint({
            "messages": current_fingerprint,
            "metadata": str(current.get("metadata_fingerprint") or ""),
            "summary_revision": int(current.get("summary_revision") or 0),
            "prompt": PROMPT_VERSION,
        })

    # -- evidence preparation ---------------------------------------------------

    def prepare_due(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        jobs = self.store.take_jobs_to_prepare(
            limit or self.settings.prepare_batch_size, lease_seconds=self.settings.prepare_lease_seconds,
        )
        return [self.prepare(job) for job in jobs]

    def prepare(self, job: dict[str, Any]) -> dict[str, Any]:
        """Read the conversation once; record pending requests directly, or store evidence for the writer."""

        resident_id = str(job.get("resident_id") or "")
        mode = str(job.get("mode") or "incremental")
        source_key = ""
        try:
            current = self.store.resident(resident_id)
            if not current or not current.get("active"):
                self.store.discard_inactive_jobs()
                return {"resident_id": resident_id, "result": "discarded"}
            if not self._global_summary_managed(resident_id):
                self.store.discard_summary_job(resident_id)
                return {"resident_id": resident_id, "result": "family_managed"}
            conversation = self.source.conversation_snapshot(resident_id)
            if str(current.get("prompt_version") or "") != PROMPT_VERSION:
                mode = "full"
            messages = list(conversation.get("messages") or [])
            current_fingerprint = message_fingerprint(messages)
            source_key = self._failure_source_key(current, current_fingerprint)
            previous_summary = current.get("summary") if isinstance(current.get("summary"), dict) else {}
            reason = str(job.get("reason") or "")
            conversation_change = reason in CONVERSATION_REASONS
            evidence = project_summary_evidence(
                messages,
                known_message_ids={str(value) for value in current.get("message_ids") or []},
                include_all_completed=(
                    mode == "full" or not conversation_change or reason == "conversation_messages_changed"
                ),
                redact=redact_text,
            )
            stable = list(evidence["stable_messages"])
            pending_requests = list(evidence["pending_requests"])
            source = {
                "cursor": int(conversation.get("cursor") or 0),
                "fingerprint": str(conversation.get("fingerprint") or fingerprint(conversation)),
                "message_fingerprint": current_fingerprint,
                "message_ids": [message_id(item) for item in messages if message_id(item)],
                "failure_key": source_key,
                "summary_revision": int(current.get("summary_revision") or 0),
            }
            if conversation_change and not stable and mode != "full":
                summary = summary_with_pending_requests(previous_summary, pending_requests)
                result = self.store.complete_pending(
                    resident_id, summary=summary, summary_text=summary_text(summary), source=source,
                    prompt_version=PROMPT_VERSION,
                )
                return {"resident_id": resident_id, "result": "pending_recorded", **result}
            projected = project_messages_for_summary(stable)
            if stable and not projected:
                raise ValueError("Nonempty summary evidence produced no readable messages")
            prepared = {
                "input": {
                    "mode": mode,
                    "resident": project_summary_metadata(current),
                    # On a writer handoff keep the member-authored summary even
                    # when there is no previous global evidence checkpoint.
                    "previous_summary": previous_summary if mode != "full" or not current.get("generation") else {},
                    "new_or_rebuild_context": projected,
                },
                "pending_requests": pending_requests,
                "evidence_refs": [message_id(item) for item in stable if message_id(item)][-40:],
                "source": source,
            }
            if not self.store.mark_ready(resident_id, prepared):
                return {"resident_id": resident_id, "result": "superseded"}
            return {"resident_id": resident_id, "result": "ready", "mode": mode, "messages": len(projected)}
        except Exception as exc:  # noqa: BLE001 - every failure backs off or parks the job
            attempts = int(job.get("attempts") or 0) + 1
            terminal = attempts >= self.settings.max_job_attempts
            self.store.fail_job(
                resident_id, f"{type(exc).__name__}: {str(exc)[:800]}",
                retry_at=after(min(3600, 30 * (2 ** min(attempts, 6)))), terminal=terminal,
                failed_source=source_key,
            )
            return {"resident_id": resident_id, "result": "failed", "error_type": type(exc).__name__,
                    "attempts": attempts, "parked": terminal}

    # -- the summary writer -----------------------------------------------------

    def claim_summary_jobs(self, holder: str, *, limit: int | None = None) -> dict[str, Any]:
        jobs = self.store.claim_ready_jobs(
            holder, limit=None if limit is None else max(1, int(limit)),
            lease_seconds=self.settings.claim_lease_seconds,
            evidence_budget=self.settings.evidence_budget_chars,
        )
        eligible = []
        for job in jobs:
            if self._global_summary_managed(job["resident_id"]):
                eligible.append(job)
            else:
                self.store.discard_summary_job(job["resident_id"])
        queue = self.store.summary_queue()
        return {"instructions": SUMMARY_INSTRUCTIONS, "summary_policy": policy(), "output_fields": list(SUMMARY_FIELDS), "jobs": eligible,
                "remaining_ready": queue["ready"], "waiting": queue["waiting"]}

    def summary_queue(self) -> dict[str, int]:
        """Job counts only, so a timer's Effect can wake the writer just when ``ready`` > 0."""

        return self.store.summary_queue()

    def submit_summary(
        self, holder: str, claim_id: str, summary: object, *, model_version: str, account_id: str,
    ) -> dict[str, Any]:
        job = self.store.claimed_job(claim_id, holder)
        evidence = job["evidence"]
        clean = validate_summary(summary)
        if not self._global_summary_managed(job["resident_id"]):
            self.store.discard_summary_job(job["resident_id"])
            raise PermissionError("Resident 已由 Family 管理，旧的全局摘要认领不能再提交")
        source = evidence.get("source") or {}
        if ("summary_revision" in source and source["summary_revision"] !=
                self.store.work_summary(job["resident_id"])["revision"]):
            self.store.discard_summary_job(job["resident_id"])
            self.store.enqueue(job["resident_id"], mode="incremental", reason="summary_revision_changed", available_at=utc_now())
            raise ValueError("工作摘要版本已变化，已重新排队，请重新认领后提交")
        clean["pending_requests"] = list(evidence.get("pending_requests") or [])
        clean["evidence_refs"] = list(evidence.get("evidence_refs") or [])
        clean["resident_id"] = str(job["resident_id"])
        clean["source_cursor"] = int(source.get("cursor") or 0)
        clean["generated_at"] = utc_now()
        result = self.store.complete_claimed(
            claim_id, holder, summary=clean, summary_text=summary_text(clean),
            model_version=model_version, account_id=account_id, prompt_version=PROMPT_VERSION,
        )
        return {**result, "status": "recorded"}

    def release_summary_job(self, holder: str, claim_id: str, reason: str) -> dict[str, Any]:
        job = self.store.claimed_job(claim_id, holder)
        attempts = int(job.get("attempts") or 0)
        source = (job["evidence"].get("source") or {})
        result = self.store.release_claim(
            claim_id, holder, f"released: {str(reason or '')[:800]}",
            retry_at=after(min(3600, 30 * (2 ** min(attempts, 6)))),
            max_attempts=self.settings.max_job_attempts,
            failed_source=str(source.get("failure_key") or ""),
        )
        return {**result, "status": "parked" if result["parked"] else "retry"}

    # -- embeddings -------------------------------------------------------------

    def process_one_embedding(self) -> dict[str, Any] | None:
        with EMBEDDING_RUNTIME_LOCK:
            if not self.embeddings_available:
                return None
            return self._process_one_embedding_enabled()

    def _process_one_embedding_enabled(self) -> dict[str, Any] | None:
        job = self.store.claim_embedding_job()
        if not job:
            return None
        resident_id = str(job.get("resident_id") or "")
        try:
            current = self.store.resident(resident_id)
            if not current or not current.get("active"):
                self.store.discard_embedding_job(resident_id)
                return {"resident_id": resident_id, "result": "discarded"}
            document = embedding_document(current)
            vector = self.embedder.embed_document(document)
            recorded = self.store.complete_embedding(
                resident_id, vector=vector, model_version=self.embedder.model_version,
                source_fingerprint=fingerprint(document),
                summary_revision=int(current.get("summary_revision") or 0),
            )
            return {"resident_id": resident_id, "result": "embedded" if recorded else "superseded", "dimensions": len(vector)}
        except Exception as exc:  # noqa: BLE001 - every failure backs off or parks the job
            attempts = int(job.get("attempts") or 0) + 1
            self.store.fail_embedding(
                resident_id, f"{type(exc).__name__}: {str(exc)[:800]}",
                retry_at=after(min(3600, 15 * (2 ** min(attempts, 7)))),
                terminal=attempts >= self.settings.max_job_attempts,
            )
            return {"resident_id": resident_id, "result": "failed", "error_type": type(exc).__name__}

    # -- reads ------------------------------------------------------------------

    def _discovery_documents(self, resident_ids: list[str] | None = None) -> list[dict[str, Any]]:
        documents = self.store.documents(resident_ids or None)
        if resident_ids is None and hasattr(self.source, "external_resident_ids"):
            visible = self.source.external_resident_ids([d["resident_id"] for d in documents])
            documents = [d for d in documents if d["resident_id"] in visible]
        elif hasattr(self.source, "personal_assistant_resident_ids"):
            private = self.source.personal_assistant_resident_ids([d["resident_id"] for d in documents])
            documents = [d for d in documents if d["resident_id"] not in private]
        return documents

    def search(
        self, *, actor: str, query: str, limit: int = 10, resident_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        actor_id, clean_query = _require(actor, query)
        requested = max(1, min(50, int(limit)))
        documents = self._discovery_documents(resident_ids)
        results = self._ranked_search(actor_id, clean_query, documents, requested)
        return {
            "schema": "tfi.resident-discovery.search.v1",
            "query": clean_query,
            "actor": actor_id,
            "results": results,
            "result_count": len(results),
            "retrieval": "hybrid" if any(item.get("vector_score") for item in results) else "lexical",
        }

    def _ranked_search(
        self, actor: str, query: str, documents: list[dict[str, Any]], limit: int,
    ) -> list[dict[str, Any]]:
        candidates = self._hybrid_candidates(query, documents, limit=max(self.settings.search_pool, limit * 5))
        visible = self.source.authorized_resident_ids(actor, [str(item["resident_id"]) for item in candidates])
        return [public_candidate(item) for item in candidates if item["resident_id"] in visible][:limit]

    def routing_candidates(
        self, *, actor: str, query: str, limit: int = 40, resident_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Authorized routing recall: ranked search interleaved with title and ownership matches."""

        actor_id, clean_query = _require(actor, query)
        bound = max(1, min(48, int(limit)))
        documents = self._discovery_documents(resident_ids)
        ranked = self._ranked_search(actor_id, clean_query, documents, bound)
        field_channels = [field_candidates(clean_query, documents, field=field, limit=bound * 2)
                          for field in ("title", "primary_ownership")]
        visible = self.source.authorized_resident_ids(
            actor_id, [str(item["resident_id"]) for channel in field_channels for item in channel],
        )
        channels = [ranked] + [
            [public_candidate(item) for item in channel if item["resident_id"] in visible]
            for channel in field_channels
        ]
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rank in range(max((len(values) for values in channels), default=0)):
            for values in channels:
                if rank >= len(values) or values[rank]["resident_id"] in seen:
                    continue
                seen.add(values[rank]["resident_id"])
                selected.append(values[rank])
                if len(selected) >= bound:
                    break
            if len(selected) >= bound:
                break
        return {
            "schema": "tfi.resident-discovery.routing-candidates.v1",
            "query": clean_query,
            "actor": actor_id,
            "results": selected,
            "result_count": len(selected),
        }

    def search_directory(self, *, actor: str, query: str = "", limit: int = 30) -> dict[str, Any]:
        """Search every indexed Resident the actor may see."""

        actor_id = str(actor or "").strip()
        if not actor_id:
            raise ValueError("actor required")
        clean_query = str(query or "").strip().casefold()[:300]
        requested = max(1, min(50, int(limit)))
        catalog = self._discovery_documents()
        visible_ids = self.source.authorized_resident_ids(actor_id, [str(item["resident_id"]) for item in catalog])
        visible = [item for item in catalog if item["resident_id"] in visible_ids]
        if clean_query:
            parts = [part for part in clean_query.split() if part]
            ranked = []
            for item in visible:
                resident_id = str(item.get("resident_id") or "").casefold()
                title = str(item.get("title") or "").casefold()
                lineage_title = str(item.get("lineage_title") or "").casefold()
                searchable = " ".join(str(item.get(field) or "").casefold() for field in (
                    "resident_id", "title", "lineage_title", "project_id", "summary_text",
                ))
                if clean_query not in searchable and not all(part in searchable for part in parts):
                    continue
                score = 10
                if clean_query in {resident_id, title}:
                    score += 100
                elif resident_id.startswith(clean_query) or title.startswith(clean_query):
                    score += 50
                elif clean_query in title:
                    score += 30
                elif clean_query in lineage_title:
                    score += 20
                score += sum(searchable.count(part) for part in parts)
                ranked.append((-score, title, resident_id, item))
            visible = [item for *_key, item in sorted(ranked, key=lambda value: value[:3])]
        else:
            visible.sort(key=lambda item: (str(item.get("title") or "").casefold(), str(item.get("resident_id") or "")))
        return {
            "schema": "tfi.resident-directory.search.v1",
            "query": str(query or "").strip()[:300],
            "results": [public_candidate(item) for item in visible[:requested]],
            "result_count": min(len(visible), requested),
            "total_visible": len(visible_ids),
        }

    def contexts(self, *, actor: str, resident_ids: list[str]) -> dict[str, Any]:
        requested = list(dict.fromkeys(str(value or "").strip() for value in resident_ids if str(value or "").strip()))
        if len(requested) > MAX_SUMMARY_RESIDENTS:
            raise ValueError(f"at most {MAX_SUMMARY_RESIDENTS} Resident contexts may be requested")
        visible = self.source.authorized_resident_ids(str(actor or "").strip(), requested)
        documents = {item["resident_id"]: item for item in self.store.documents([rid for rid in requested if rid in visible])}
        return {
            "schema": "tfi.resident-context.snapshots.v1",
            "residents": [public_candidate(documents[rid]) for rid in requested if rid in documents],
        }

    def request_rebuild(self, *, actor: str, resident_id: str, reason: str = "manual") -> dict[str, Any]:
        resident_id = str(resident_id or "").strip()
        if resident_id not in self.source.authorized_resident_ids(str(actor or "").strip(), [resident_id]):
            raise PermissionError("Resident is not visible to the current actor")
        current = self.store.resident(resident_id)
        if not current or not current.get("active"):
            raise KeyError("Resident not found")
        if not self._global_summary_managed(resident_id):
            raise ValueError("Family 摘要由成员本人或门面的 Steward 维护，无需请求全局重建")
        self.store.enqueue(resident_id, mode="full", reason=f"manual:{reason}"[:300], available_at=utc_now())
        return {"schema": "tfi.resident-context.rebuild-requested.v1", "resident_id": resident_id, "queued": True}

    def submit_routing_eval_correction(
        self, *, actor: str, query: str, predicted: list[dict[str, Any]],
        ground_truth_resident_id: str, ground_truth_action: str, rationale: str = "",
    ) -> dict[str, Any]:
        actor_id = str(actor or "").strip()
        clean_query = str(query or "").strip()[:4000]
        resident_id = str(ground_truth_resident_id or "").strip()
        action = str(ground_truth_action or "").strip()
        if not actor_id or not clean_query:
            raise ValueError("actor and query are required")
        if action not in {"open", "fork"}:
            raise ValueError("ground_truth_action must be open or fork")
        predicted_items = [item for item in list(predicted or [])[:8] if isinstance(item, dict)]
        visible = self.source.authorized_resident_ids(
            actor_id, [resident_id] + [str(item.get("resident_id") or "").strip() for item in predicted_items],
        )
        if resident_id not in visible:
            raise PermissionError("Ground-truth Resident is not visible to the current actor")
        record = self.store.resident(resident_id)
        if not record or not record.get("active"):
            raise KeyError("Ground-truth Resident not found")
        compact = []
        for item in predicted_items:
            predicted_id = str(item.get("resident_id") or "").strip()
            if predicted_id not in visible:
                continue
            predicted_action = str(item.get("recommended_action") or "open").strip()
            compact.append({
                "resident_id": predicted_id,
                "title": str(item.get("title") or "")[:200],
                "recommended_action": predicted_action if predicted_action in {"open", "fork"} else "open",
                "reason": str(item.get("reason") or "")[:800],
            })
        clean_rationale = str(rationale or "").strip()[:1200]
        digest = hashlib.sha256(f"{clean_query}\n{resident_id}\n{action}".encode("utf-8")).hexdigest()[:16]
        dataset = self.store.add_routing_eval_case({
            "case_id": f"portal-correction-{digest}",
            "query": clean_query,
            "ground_truth": {"resident_ids": [resident_id], "recommended_action": action},
            "tags": ["portal-correction", "production-regression"],
            "rationale": clean_rationale,
        })
        submission = self.store.record_routing_eval_submission({
            "submission_id": f"routing_eval_{secrets.token_hex(12)}",
            "query": clean_query,
            "predicted": compact,
            "ground_truth_resident_id": resident_id,
            "ground_truth_action": action,
            "rationale": clean_rationale,
            "submitted_by": actor_id,
            "dataset_case_id": dataset["case_id"],
            "dataset_added": dataset["added"],
        })
        return {
            "schema": "tfi.resident-routing-eval-correction.v1",
            "submission": submission,
            "dataset_added": bool(dataset["added"]),
        }

    # -- ranking ------------------------------------------------------------------

    def _hybrid_candidates(self, query: str, documents: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        with EMBEDDING_RUNTIME_LOCK:
            return self._hybrid_candidates_locked(query, documents, limit=limit)

    def _hybrid_candidates_locked(self, query: str, documents: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        lexical = lexical_candidates(query, documents, limit=limit)
        vector = []
        if self.embeddings_available:
            try:
                vector = self._vector_candidates(query, documents, limit=limit)
            except Exception:  # noqa: BLE001 - search degrades to lexical without the model
                pass
        combined: dict[str, dict[str, Any]] = {}
        scores: dict[str, float] = {}
        sources: dict[str, set[str]] = {}
        for source, weight, values in (("lexical", 0.45, lexical), ("vector", 0.55, vector)):
            for rank, item in enumerate(values, start=1):
                resident_id = str(item["resident_id"])
                combined[resident_id] = {**combined.get(resident_id, {}), **item}
                scores[resident_id] = scores.get(resident_id, 0.0) + weight / (60.0 + rank)
                sources.setdefault(resident_id, set()).add(source)
        normalized_query = query.strip().casefold()
        for resident_id, item in combined.items():
            if resident_id.casefold() == normalized_query:
                scores[resident_id] += 1.0
            title = str(item.get("title") or "").strip().casefold()
            if len(title) >= 3 and title in normalized_query:
                # An exact title mention identifies the object, not a topic.
                scores[resident_id] += 1.0
            item["hybrid_score"] = round(scores[resident_id], 8)
            item["retrieval_sources"] = sorted(sources[resident_id])
        return sorted(
            combined.values(),
            key=lambda item: (-float(item["hybrid_score"]), str(item.get("title") or ""), str(item["resident_id"])),
        )[:max(1, int(limit))]

    def _vector_candidates(self, query: str, documents: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        by_id = {str(item["resident_id"]): item for item in documents}
        vectors = self.store.vectors(self.embedder.model_version, by_id)
        if not vectors:
            return []
        query_vector = self.embedder.embed_query(query)
        scored = []
        for resident_id, vector in vectors.items():
            if len(vector) != len(query_vector):
                continue
            score = float(sum(left * right for left, right in zip(query_vector, vector)))
            item = dict(by_id[resident_id])
            item["vector_score"] = round(score, 6)
            scored.append((-score, str(item.get("title") or ""), resident_id, item))
        scored.sort(key=lambda value: value[:3])
        return [item for *_key, item in scored[:max(1, int(limit))]]


def _require(actor: str, query: str) -> tuple[str, str]:
    actor_id = str(actor or "").strip()
    clean_query = str(query or "").strip()
    if not actor_id:
        raise ValueError("actor required")
    if not clean_query:
        raise ValueError("query required")
    return actor_id, clean_query


def tokens(text: str) -> set[str]:
    """Latin words and CJK bigrams."""

    clean = str(text or "").casefold()
    output = set(re.findall(r"[a-z0-9_./+\-]{2,}", clean))
    for segment in re.findall(r"[㐀-鿿]+", clean):
        if len(segment) == 1:
            output.add(segment)
        else:
            output.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return output


def lexical_candidates(query: str, documents: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    query_tokens = tokens(query)
    normalized = query.strip().casefold()
    scored = []
    for document in documents:
        title = str(document.get("title") or "").casefold()
        text = " ".join(str(document.get(key) or "") for key in (
            "title", "lineage_title", "project_id", "summary_text",
        ))
        candidate_tokens = tokens(text)
        overlap = len(query_tokens & candidate_tokens) / max(1, len(query_tokens))
        exact = 2.0 if normalized == str(document.get("resident_id") or "").casefold() else 0.0
        # An explicit Resident title in the request is a stronger routing
        # signal than topical overlap, even before a summary exists.
        title_match = 1.25 if len(title) >= 3 and title in normalized else 0.0
        freshness = 0.0 if document.get("stale") is not False else 0.08
        score = exact + title_match + overlap + freshness
        if exact or title_match or overlap:
            item = dict(document)
            item["search_score"] = round(score, 6)
            item["matched_tokens"] = sorted(query_tokens & candidate_tokens)[:20]
            scored.append((-score, title, str(document["resident_id"]), item))
    scored.sort(key=lambda value: value[:3])
    return [item for *_key, item in scored[:max(1, int(limit))]]


def field_candidates(
    query: str, documents: list[dict[str, Any]], *, field: str, limit: int,
) -> list[dict[str, Any]]:
    """Match one routing field (title or primary ownership) by token F1."""

    if field not in {"title", "primary_ownership"}:
        raise ValueError(f"unsupported discovery field: {field}")
    query_tokens = tokens(query)
    scored = []
    for document in documents:
        if field == "title":
            values = [str(document.get("title") or "")]
        else:
            summary = document.get("summary") if isinstance(document.get("summary"), dict) else {}
            ownership = summary.get("primary_ownership")
            values = [str(value) for value in ownership or []]
        field_tokens = tokens(" ".join(values))
        matched = query_tokens & field_tokens
        if not matched:
            continue
        precision = len(matched) / max(1, len(field_tokens))
        recall = len(matched) / max(1, len(query_tokens))
        score = 2.0 * precision * recall / max(precision + recall, 1e-12)
        item = dict(document)
        item[f"{field}_score"] = round(score, 6)
        item[f"{field}_matched_tokens"] = sorted(matched)[:20]
        scored.append((-score, str(document.get("title") or ""), str(document["resident_id"]), item))
    scored.sort(key=lambda value: value[:3])
    return [item for *_key, item in scored[:max(1, int(limit))]]


def public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    generation = int(item.get("summary_revision") or item.get("generation") or 0)
    return {
        "resident_id": str(item.get("resident_id") or ""),
        "title": str(item.get("title") or ""),
        "lineage_id": str(item.get("lineage_id") or ""),
        "lineage_title": str(item.get("lineage_title") or ""),
        "project_id": str(item.get("project_id") or ""),
        "status": str(item.get("status") or ""),
        "summary": summary,
        "summary_text": str(item.get("summary_text") or ""),
        "summary_generation": generation,
        "summary_stale": not generation or item.get("stale") is not False,
        "summary_updated_at": str(item.get("summary_updated_at") or ""),
        "search_score": float(item.get("search_score") or 0),
        "matched_tokens": list(item.get("matched_tokens") or []),
        "vector_score": float(item.get("vector_score") or 0),
        "hybrid_score": float(item.get("hybrid_score") or 0),
        "retrieval_sources": list(item.get("retrieval_sources") or []),
    }


def embedding_document(item: dict[str, Any]) -> str:
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    parts = [
        f"Resident: {item.get('title') or item.get('resident_id') or ''}",
        f"Lineage: {item.get('lineage_title') or ''}",
        f"Current focus: {summary.get('current_focus') or ''}",
        f"Branch delta: {summary.get('branch_delta') or ''}",
    ]
    for key in (
        "active_threads", "durable_expertise", "recent_results",
        "primary_ownership", "supporting_expertise", "route_when", "avoid_when",
    ):
        values = summary.get(key) if isinstance(summary.get(key), list) else []
        if values:
            parts.append(f"{key}: " + "; ".join(str(value) for value in values[:16]))
    return "\n".join(part[:2500] for part in parts if part.strip())[:12000]


def _older_than(value: str, *, days: int) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    return parsed <= datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))


_SHARED: ResidentContext | None = None


def resident_context() -> ResidentContext:
    """The process's Resident discovery, sharing one store and one embedder."""

    global _SHARED
    if _SHARED is None:
        _SHARED = ResidentContext()
    return _SHARED
