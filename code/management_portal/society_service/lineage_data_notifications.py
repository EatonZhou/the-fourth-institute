"""Human notifications: per Lineage, and the one record that belongs to no Lineage."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from management_common.json_columns import json_text
from .lineage_data_columns import stored_object


class HumanNotificationRecords:
    """Reads and writes of human notification state."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def human_notification_records(
        self, lineage_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        requested = None if lineage_ids is None else list(dict.fromkeys(lineage_ids))
        if requested == []:
            return []
        batches = [None] if requested is None else [
            requested[offset:offset + 400] for offset in range(0, len(requested), 400)
        ]
        records = []
        with self.database.read() as connection:
            for batch in batches:
                restriction = "" if batch is None else (
                    " AND notice.lineage_id IN (" + ",".join("%s" for _ in batch) + ")"
                )
                rows = connection.execute(
                    "SELECT notice.lineage_id, lineage.title, notice.notifications_json AS state_json "
                    "FROM lineage_human_notifications AS notice "
                    "JOIN lineages AS lineage ON lineage.lineage_id = notice.lineage_id "
                    "WHERE TRUE"
                    + restriction + " ORDER BY notice.lineage_id", batch or [],
                ).fetchall()
                for row in rows:
                    records.append({
                        "lineage_id": str(row["lineage_id"]),
                        "title": str(row["title"]),
                        "runtime": stored_object(row["state_json"], str(row["lineage_id"])),
                    })
            if requested is None:
                row = connection.execute(
                    "SELECT notifications_json FROM portal_human_notifications WHERE singleton=1"
                ).fetchone()
                if row:
                    records.append({"lineage_id": "", "title": "",
                                    "runtime": stored_object(row["notifications_json"], "portal_human_notifications")})
        return records

    @contextmanager
    def portal_human_notifications(self) -> Iterator[dict[str, Any]]:
        """The one record of human notifications that belong to no Lineage."""
        with self.database.transaction() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':portal_human_notifications', 0))"
            )
            row = connection.execute(
                "SELECT notifications_json FROM portal_human_notifications WHERE singleton=1"
            ).fetchone()
            original = stored_object(row["notifications_json"], "portal_human_notifications") if row else {}
            state = deepcopy(original)
            yield state
            if state != original:
                connection.execute(
                    "INSERT INTO portal_human_notifications(singleton,notifications_json) VALUES (1,%s) "
                    "ON CONFLICT(singleton) DO UPDATE SET notifications_json=excluded.notifications_json",
                    (json_text(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False),),
                )
