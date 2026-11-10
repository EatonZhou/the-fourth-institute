"""Personal successful-reader opens, owned within presentation associations.

Only the explicit reader-open acknowledgement writes history; discovery and
preview never call this owner. Favorites are independent associations.
"""
import base64
from datetime import datetime
import json
import re

from management_portal import paper_favorites, presentation_store
from management_portal.paper_search import resource_index_papers_by_ids
from management_portal.resources import get_resource_service
from management_portal.resources.directory_registrations import is_deleted

NAMESPACE = "paper.recent_reading"


class PrincipalChanged(ValueError):
    """The reading began under a different principal; no state was written."""


def _actor(actor):
    if not str(actor or "").strip():
        raise PermissionError("authenticated principal required")
    return actor


def record_open(payload, *, actor):
    _actor(actor)
    if not isinstance(payload, dict) or set(payload) != {"paper_id", "expected_principal"}:
        raise ValueError("paper_id and expected_principal are required")
    expected = payload["expected_principal"]
    if not isinstance(expected, str) or not expected:
        raise ValueError("expected_principal must be a nonempty principal ID")
    if expected != actor:
        raise PrincipalChanged("session_changed")
    identifier = payload["paper_id"]
    if not isinstance(identifier, str) or not re.fullmatch(r"paper_[0-9a-f]{12}", identifier):
        raise ValueError("valid paper_id required")
    if is_deleted(identifier):
        raise KeyError(identifier)
    get_resource_service().get(identifier, actor=actor)
    with presentation_store.database().transaction("recent:" + actor + ":" + identifier) as connection:
        row = connection.execute("""INSERT INTO associations(actor,namespace,association_key,value_json,updated_at)
            VALUES (%s,%s,%s,'{}',clock_timestamp())
            ON CONFLICT(actor,namespace,association_key) DO UPDATE
            SET updated_at=GREATEST(associations.updated_at,clock_timestamp())
            RETURNING updated_at""", (actor, NAMESPACE, identifier)).fetchone()
    return {"paper_id": identifier, "last_read_at": row[0].isoformat()}


def _cursor(value):
    if not value:
        return None
    try:
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError()
        timestamp, identifier = json.loads(base64.urlsafe_b64decode(value.encode()).decode())
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None or not re.fullmatch(r"paper_[0-9a-f]{12}", identifier):
            raise ValueError()
        return timestamp, identifier
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid recent-reading cursor") from exc


def _encode(row):
    return base64.urlsafe_b64encode(json.dumps([row["updated_at"].isoformat(), row["association_key"]]).encode()).decode()


def list_recent(params, *, actor):
    _actor(actor)
    if not isinstance(params, dict) or set(params) - {"limit", "cursor"}:
        raise ValueError("only limit and cursor are accepted")
    try:
        limit = int(params.get("limit", 50))
        if isinstance(params.get("limit"), bool) or not 1 <= limit <= 500:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be between 1 and 500") from exc
    after = _cursor(params.get("cursor"))
    favorites = set(paper_favorites.favorite_ids(actor))
    visible = []
    with presentation_store.database().read() as connection:
        while len(visible) <= limit:
            condition, values = "", [actor, NAMESPACE]
            if after:
                condition = " AND (updated_at,association_key) < (%s::timestamptz,%s)"
                values.extend(after)
            rows = connection.execute("SELECT association_key,updated_at FROM associations "
                "WHERE actor=%s AND namespace=%s" + condition +
                " ORDER BY updated_at DESC,association_key DESC LIMIT %s", (*values, limit + 1)).fetchall()
            if not rows:
                break
            ids = [row["association_key"] for row in rows if not is_deleted(row["association_key"])]
            papers = {paper["id"]: paper for paper in resource_index_papers_by_ids(ids, actor=actor)}
            for row in rows:
                identifier = row["association_key"]
                if identifier in papers:
                    visible.append((row, {**papers[identifier], "last_read_at": row["updated_at"].isoformat(),
                                          "favorite": identifier in favorites}))
                    if len(visible) > limit:
                        break
            after = (rows[-1]["updated_at"].isoformat(), rows[-1]["association_key"])
            if len(rows) < limit + 1:
                break
    return {"papers": [paper for _, paper in visible[:limit]],
            "next_cursor": _encode(visible[limit - 1][0]) if len(visible) > limit else ""}
