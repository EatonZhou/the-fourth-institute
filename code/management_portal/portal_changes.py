"""What a batch of Portal change notifications tells one subscriber.

The Portal's change stream (``/api/events``) gives each subscriber
the targets that changed in the domains it follows.  ``PortalChanges`` sorts
the notifications by domain (``add``), keeps only what the subscriber may
read (``authorize``) and builds the payload it receives (``payload``).
"""
from __future__ import annotations

from typing import Any, Callable


class PortalChanges:
    def __init__(self, actor: str, domains: set[str], *, on_active_agents_snapshot: Callable[[], None]) -> None:
        self.actor = actor
        self.domains = domains
        self.on_active_agents_snapshot = on_active_agents_snapshot
        self.snapshot_required = False
        self.candidates: dict[str, set[str]] = {
            "execution": set(),
            "resident": set(),
            "lineage": set(),
            "projection": set(),
        }
        self.projection_candidates: dict[str, set[str]] = {}
        self.projection_fields: dict[str, dict[str, set[str]]] = {}
        self.projection_details: dict[str, dict[str, dict[str, object]]] = {}
        self.resource_projection_candidates: dict[str, set[str]] = {}
        self.resource_projection_snapshots: set[str] = set()
        self.resource_projection_changes: dict[str, list[str]] = {}
        self.resident_events: list[dict[str, object]] = []
        self.visible_resident_events: list[dict[str, object]] = []
        self.resident_changes: dict[str, set[str]] = {}
        self.changed_domains: set[str] = set()
        self._handlers: dict[str, Callable[[Any, str, str, str], None]] = {
            "execution": self._add_execution,
            "projection": self._add_projection,
            "resident": self._add_collaboration,
            "lineage": self._add_collaboration,
            "resource": self._add_resource,
        }

    def add(self, notification: Any, domain: str, target_id: str) -> None:
        """Sort one notification, whose subject names ``domain`` and ``target_id``."""
        subject = str(notification.subject or "")
        if subject == "portal.gateway.reconnected":
            self.snapshot_required = True
            return
        if domain not in self.domains:
            return
        handler = self._handlers.get(domain)
        if handler is not None:
            handler(notification, subject, domain, target_id)

    def _add_execution(self, notification: Any, _subject: str, domain: str, target_id: str) -> None:
        event_actor = str(notification.payload.get("actor") or "")
        if not event_actor or event_actor.casefold() != self.actor.casefold():
            return
        if target_id:
            self.candidates[domain].add(target_id)
        self.changed_domains.add(domain)

    def _add_projection(self, notification: Any, subject: str, domain: str, target_id: str) -> None:
        if not self._addressed_to_actor(notification.payload):
            return
        parts = subject.split(".")
        projection = parts[3] if len(parts) >= 5 else ""
        if projection == "active-agents" and target_id == "snapshot":
            self.on_active_agents_snapshot()
        if target_id:
            self.candidates[domain].add(target_id)
            if projection:
                self._add_projection_target(notification.payload, projection, target_id)
        self.changed_domains.add(domain)

    def _addressed_to_actor(self, payload: dict[str, Any]) -> bool:
        """A projection change names its viewers (``actors``) or the one actor it is for."""
        event_actor = str(payload.get("actor") or "")
        event_actors = {
            str(item).strip().casefold()
            for item in (
                payload.get("actors")
                if isinstance(payload.get("actors"), list)
                else []
            )
            if str(item).strip()
        }
        if event_actors and self.actor.casefold() not in event_actors:
            return False
        if not event_actors and event_actor and event_actor.casefold() != self.actor.casefold():
            return False
        return True

    def _add_projection_target(self, payload: dict[str, Any], projection: str, target_id: str) -> None:
        self.projection_candidates.setdefault(projection, set()).add(
            target_id
        )
        fields = payload.get("fields")
        if isinstance(fields, list):
            target_fields = self.projection_fields.setdefault(
                projection,
                {},
            ).setdefault(target_id, set())
            target_fields.update(
                str(field).strip()
                for field in fields
                if str(field).strip()
            )
        details = payload.get("details")
        if isinstance(details, dict) and details:
            target_details = self.projection_details.setdefault(
                projection,
                {},
            ).setdefault(target_id, {})
            target_details.update(details)

    def _add_collaboration(self, notification: Any, subject: str, domain: str, target_id: str) -> None:
        if target_id:
            self.candidates[domain].add(target_id)
        if domain != "resident":
            return
        parts = subject.split(".")
        if target_id and len(parts) == 5:
            self.resident_changes.setdefault(target_id, set()).add(parts[4])
            if parts[4] == "identity" and notification.payload.get("kind") == "deleted":
                self.resident_changes[target_id].add("deleted")
        event = notification.payload.get("event")
        if (
            notification.payload.get("kind") == "conversation_event"
            and isinstance(event, dict)
            and str(event.get("resident_id") or "") == target_id
        ):
            self.resident_events.append(dict(event))

    def _add_resource(self, notification: Any, subject: str, domain: str, _target_id: str) -> None:
        parts = subject.split(".")
        if len(parts) >= 5 and parts[3] == "projection":
            adapter = parts[4]
            resource_ids = {
                str(item or "").strip()
                for item in notification.payload.get("resource_ids") or []
                if str(item or "").strip()
            }
            if notification.payload.get("snapshot_required") or not resource_ids:
                self.resource_projection_snapshots.add(adapter)
            else:
                self.resource_projection_candidates.setdefault(adapter, set()).update(
                    resource_ids
                )
            return
        self.changed_domains.add(domain)
        if len(parts) >= 5 and parts[3] in {"resident", "lineage"}:
            self.candidates[parts[3]].add(parts[4])

    def authorize(self) -> None:
        """Keep the Residents, Lineages, conversations and resources the subscriber may read.

        A failed permission lookup discloses nothing and asks for a snapshot.
        """
        self._authorize_collaboration()
        self._authorize_resident_events()
        self._authorize_resource_projections()

    def _authorize_collaboration(self) -> None:
        candidates = self.candidates
        if not candidates["resident"] | candidates["lineage"]:
            return
        try:
            from management_portal.permission_system import readable_ids
            for domain, instruction in (("resident", "GetResident"), ("lineage", "GetLineage")):
                candidates[domain] = readable_ids(instruction, domain + "_id", candidates[domain], actor=self.actor)
        except Exception:
            candidates["resident"] = set()
            candidates["lineage"] = set()
            self.snapshot_required = True
        for domain in ("resident", "lineage"):
            if candidates[domain] and domain in self.domains:
                self.changed_domains.add(domain)
        # Deletion or a permission change can remove an identity from the
        # authorization results above. Existing Editors still need to revalidate their own
        # targets; disclose no removed/private identity to other subscribers.
        if "resident" in self.domains and any(
            "identity" in scopes and resident_id not in candidates["resident"]
            for resident_id, scopes in self.resident_changes.items()
        ):
            self.snapshot_required = True

    def _authorize_resident_events(self) -> None:
        try:
            from management_portal.permission_system import readable_ids
            conversation_ids = readable_ids("GetConversation", "resident_id",
                                            [event.get("resident_id") for event in self.resident_events], actor=self.actor)
        except Exception:
            conversation_ids = set()
            self.snapshot_required = True
        self.visible_resident_events = [
            event for event in self.resident_events
            if str(event.get("resident_id") or "") in self.candidates["resident"]
            and str(event.get("resident_id") or "") in conversation_ids
            and "resident" in self.domains
        ]

    def _authorize_resource_projections(self) -> None:
        resource_projection_ids = sorted({
            resource_id
            for resource_ids in self.resource_projection_candidates.values()
            for resource_id in resource_ids
        })
        if resource_projection_ids:
            try:
                from management_portal.permission_system import readable_ids
                readable = readable_ids("ResolveResource", "address", resource_projection_ids, actor=self.actor)
            except Exception:
                readable = set()
                self.resource_projection_snapshots.update(self.resource_projection_candidates)
            for adapter, resource_ids in self.resource_projection_candidates.items():
                visible_ids = sorted(resource_ids & readable)
                if visible_ids:
                    self.resource_projection_changes[adapter] = visible_ids
        if self.resource_projection_changes or self.resource_projection_snapshots:
            self.changed_domains.add("resource")

    @property
    def inbox_changed(self) -> bool:
        """Whether the subscriber's cached inbox (Agent conversation index) is stale."""
        return "agent-conversation-index" in self.projection_candidates or self.snapshot_required

    def payload(self) -> dict:
        domains = self.domains
        return {
            "domains": sorted(self.changed_domains),
            "changed": {
                domain: sorted(resource_ids)
                for domain, resource_ids in self.candidates.items()
                if resource_ids and domain in domains
            },
            "projections": {
                projection: sorted(target_ids)
                for projection, target_ids in sorted(self.projection_candidates.items())
                if target_ids
            },
            "projection_changes": {
                projection: {
                    target_id: sorted(
                        self.projection_fields.get(projection, {}).get(target_id, set())
                    )
                    for target_id in sorted(target_ids)
                }
                for projection, target_ids in sorted(self.projection_candidates.items())
                if target_ids
            },
            "projection_details": {
                projection: {
                    target_id: dict(details)
                    for target_id, details in sorted(target_details.items())
                    if details
                }
                for projection, target_details in sorted(self.projection_details.items())
                if target_details
            },
            "resource_projection_changes": {
                adapter: resource_ids
                for adapter, resource_ids in sorted(
                    self.resource_projection_changes.items()
                )
            },
            "resource_projection_snapshots": sorted(self.resource_projection_snapshots),
            "resident_events": self.visible_resident_events,
            "resident_changes": {
                resident_id: sorted(scopes)
                for resident_id, scopes in sorted(self.resident_changes.items())
                if resident_id in self.candidates["resident"] and "resident" in domains
            },
            "snapshot_required": self.snapshot_required,
        }
