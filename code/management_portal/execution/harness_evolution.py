"""Harness policy evolution: champion and challenger parameters per load cell.

A cell is one Provider and one load family (``harness_effectiveness.load_family``).
Its champion is the set of tunable parameters every turn of the cell runs with,
as overrides of the Backend's parameters (``harness_policy_table``); an empty
champion is the Backend's parameters themselves.  A cell may run one
challenger, which differs from the champion in one tunable, on a share of the
sessions.  The daily evaluation (``harness_evolution_evaluation``) compares the
two arms on the turns of daily use and promotes, keeps or retires the
challenger.

Each cell is one row; every change of a cell is also appended to the event
log with its evidence and who made it.  Turns read the cells from a
per-process copy refreshed once a minute, so choosing an arm costs no
database round trip.

``harness_turn_calls`` keeps the model calls of recent turns for the offline
replay of compaction limits (``harness_evolution_offline``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from management_common.database import DatabaseError
from management_common.json_columns import json_text
from management_common.portal_database import portal_database
from management_common.database_time import instant, epoch_seconds

from .harness_policy_table import validate_overrides


EVOLUTION_SCHEMA_NAME = "harness_evolution"
CHAMPION = "champion"
CHALLENGER = "challenger"
# A cell runs its champion only: no challenger, or a challenger whose result
# waits for a person to confirm it keeps running.
STABLE = "stable"
EXPLORING = "exploring"
PROMOTION_RECOMMENDED = "promotion_recommended"
# Nothing changes in a frozen cell, by evaluation or by proposal.
FROZEN = "frozen"
CELL_STATUSES = frozenset({STABLE, EXPLORING, PROMOTION_RECOMMENDED, FROZEN})
DEFAULT_CHALLENGER_SHARE = 0.10
MAXIMUM_CHALLENGER_SHARE = 0.25
CELL_REFRESH_SECONDS = 60.0


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS harness_policy_cells (
    provider TEXT NOT NULL,
    workload_family TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    champion_json JSONB NOT NULL,
    challenger_json JSONB NOT NULL,
    challenger_share DOUBLE PRECISION NOT NULL,
    challenger_since TIMESTAMPTZ,
    evaluation_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, workload_family)
);
CREATE TABLE IF NOT EXISTS harness_policy_events (
    event_id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    workload_family TEXT NOT NULL,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    champion_json JSONB NOT NULL,
    challenger_json JSONB NOT NULL,
    evidence_json JSONB NOT NULL,
    actor TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS harness_policy_events_cell_idx
ON harness_policy_events(provider, workload_family, recorded_at);
CREATE TABLE IF NOT EXISTS harness_turn_calls (
    execution_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    cell TEXT NOT NULL,
    arm TEXT NOT NULL,
    version INTEGER NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    compaction_json JSONB NOT NULL,
    calls_json JSONB NOT NULL,
    compactions_json JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS harness_turn_calls_recorded_idx
ON harness_turn_calls(recorded_at);
"""


@dataclass(frozen=True)
class PolicyCell:
    provider: str
    workload_family: str
    version: int = 0
    status: str = STABLE
    champion: dict[str, Any] = field(default_factory=dict)
    challenger: dict[str, Any] | None = None
    challenger_share: float = 0.0
    challenger_since: float = 0.0
    evaluation: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.workload_family}"

    def report(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "workload_family": self.workload_family,
            "version": self.version,
            "status": self.status,
            "champion": dict(self.champion),
            "challenger": dict(self.challenger) if self.challenger is not None else None,
            "challenger_share": self.challenger_share,
            "challenger_since": self.challenger_since,
            "evaluation": dict(self.evaluation),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ArmAssignment:
    """The arm one turn runs and the parameter overrides it brings."""

    cell: str
    arm: str
    version: int
    overrides: dict[str, Any]

    def signals(self) -> dict[str, Any]:
        return {
            "policy_cell": self.cell,
            "policy_arm": self.arm,
            "policy_version": self.version,
            "policy_overrides": dict(self.overrides),
        }


def assign_arm(cell: PolicyCell | None, *, provider: str, workload_family: str, session_key: str) -> ArmAssignment:
    """Choose the arm of one turn.

    A session stays on one arm for as long as the cell keeps its version, so a
    conversation never switches its parameters from turn to turn.
    """

    key = f"{provider}/{workload_family}"
    if cell is None:
        return ArmAssignment(key, CHAMPION, 0, {})
    if (
        cell.challenger is not None
        and cell.status in {EXPLORING, PROMOTION_RECOMMENDED}
        and cell.challenger_share > 0
        and _bucket(f"{session_key}|{key}|{cell.version}") < cell.challenger_share
    ):
        return ArmAssignment(key, CHALLENGER, cell.version, dict(cell.challenger))
    return ArmAssignment(key, CHAMPION, cell.version, dict(cell.champion))


def _bucket(text: str) -> float:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


class HarnessEvolutionStore:
    """The cells and their event log, in the ``harness_evolution`` schema."""

    def __init__(self) -> None:
        self.database = portal_database(schema=EVOLUTION_SCHEMA_NAME)
        self._initialized = False
        self._init_lock = threading.Lock()

    def ensure_schema(self) -> None:
        with self._connect():
            pass

    # Reading -------------------------------------------------------------

    def cells(self) -> list[PolicyCell]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM harness_policy_cells ORDER BY provider, workload_family"
            ).fetchall()
        return [_cell_from_row(row) for row in rows]

    def cell(self, provider: str, workload_family: str) -> PolicyCell | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM harness_policy_cells WHERE provider=%s AND workload_family=%s",
                (provider, workload_family),
            ).fetchone()
        return _cell_from_row(row) if row else None

    def events(
        self,
        *,
        provider: str = "",
        workload_family: str = "",
        since: float = 0.0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["recorded_at >= %s"]
        values: list[object] = [instant(since)]
        if provider:
            clauses.append("provider=%s")
            values.append(provider)
        if workload_family:
            clauses.append("workload_family=%s")
            values.append(workload_family)
        values.append(max(1, min(5000, int(limit))))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM harness_policy_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY event_id DESC LIMIT %s",
                values,
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "provider": str(row["provider"]),
                "workload_family": str(row["workload_family"]),
                "kind": str(row["kind"]),
                "version": int(row["version"]),
                "champion": _decode(row["champion_json"]) or {},
                "challenger": _decode(row["challenger_json"]),
                "evidence": _decode(row["evidence_json"]) or {},
                "actor": str(row["actor"]),
                "recorded_at": epoch_seconds(row["recorded_at"]),
            }
            for row in rows
        ]

    # Changing --------------------------------------------------------------

    def start_challenger(
        self,
        provider: str,
        workload_family: str,
        overrides: Mapping[str, object],
        *,
        actor: str,
        share: float = DEFAULT_CHALLENGER_SHARE,
        evidence: Mapping[str, object] | None = None,
    ) -> PolicyCell:
        """Run ``overrides`` (the whole parameter set) as the cell's challenger."""

        challenger = validate_overrides(overrides)
        share = float(share)
        if not 0 < share <= MAXIMUM_CHALLENGER_SHARE:
            raise ValueError(
                f"challenger share must be in (0, {MAXIMUM_CHALLENGER_SHARE}]"
            )

        def change(cell: PolicyCell) -> tuple[PolicyCell, str]:
            if cell.status == FROZEN:
                raise ValueError(f"cell {cell.key} is frozen")
            if cell.challenger is not None:
                raise ValueError(f"cell {cell.key} already runs a challenger")
            if challenger == cell.champion:
                raise ValueError("the challenger equals the champion")
            return _replace(
                cell,
                version=cell.version + 1,
                status=EXPLORING,
                challenger=challenger,
                challenger_share=share,
                challenger_since=time.time(),
                evaluation={},
            ), "challenger_started"

        return self._change(provider, workload_family, change, actor=actor, evidence=evidence)

    def record_evaluation(
        self,
        provider: str,
        workload_family: str,
        evaluation: Mapping[str, object],
        *,
        actor: str,
        recommend_promotion: bool = False,
    ) -> PolicyCell:
        def change(cell: PolicyCell) -> tuple[PolicyCell, str]:
            if cell.challenger is None:
                raise ValueError(f"cell {cell.key} runs no challenger")
            if recommend_promotion and cell.status == EXPLORING:
                return _replace(
                    cell, status=PROMOTION_RECOMMENDED, evaluation=dict(evaluation),
                ), "promotion_recommended"
            return _replace(cell, evaluation=dict(evaluation)), "evaluated"

        return self._change(provider, workload_family, change, actor=actor, evidence=evaluation)

    def promote(
        self,
        provider: str,
        workload_family: str,
        *,
        actor: str,
        evidence: Mapping[str, object] | None = None,
    ) -> PolicyCell:
        """Make the challenger the champion of the cell."""

        def change(cell: PolicyCell) -> tuple[PolicyCell, str]:
            if cell.challenger is None:
                raise ValueError(f"cell {cell.key} runs no challenger")
            return _replace(
                cell,
                version=cell.version + 1,
                status=STABLE,
                champion=dict(cell.challenger),
                challenger=None,
                challenger_share=0.0,
                challenger_since=0.0,
            ), "promoted"

        return self._change(provider, workload_family, change, actor=actor, evidence=evidence)

    def retire(
        self,
        provider: str,
        workload_family: str,
        kind: str,
        *,
        actor: str,
        evidence: Mapping[str, object] | None = None,
    ) -> PolicyCell:
        """End the challenger without promoting it (``kind`` says why)."""

        if kind not in {"rolled_back", "rejected", "inconclusive", "withdrawn"}:
            raise ValueError(f"not a way to retire a challenger: {kind}")

        def change(cell: PolicyCell) -> tuple[PolicyCell, str]:
            if cell.challenger is None:
                raise ValueError(f"cell {cell.key} runs no challenger")
            return _replace(
                cell,
                version=cell.version + 1,
                status=STABLE if cell.status != FROZEN else FROZEN,
                challenger=None,
                challenger_share=0.0,
                challenger_since=0.0,
            ), kind

        return self._change(provider, workload_family, change, actor=actor, evidence=evidence)

    def freeze(self, provider: str, workload_family: str, *, actor: str, frozen: bool = True) -> PolicyCell:
        """Stop or resume all automatic changes of a cell.  Freezing ends its challenger."""

        def change(cell: PolicyCell) -> tuple[PolicyCell, str]:
            if frozen:
                return _replace(
                    cell,
                    version=cell.version + (1 if cell.challenger is not None else 0),
                    status=FROZEN,
                    challenger=None,
                    challenger_share=0.0,
                    challenger_since=0.0,
                ), "frozen"
            if cell.status != FROZEN:
                raise ValueError(f"cell {cell.key} is not frozen")
            return _replace(cell, status=STABLE), "unfrozen"

        return self._change(provider, workload_family, change, actor=actor, evidence=None)

    def _change(
        self,
        provider: str,
        workload_family: str,
        change: Any,
        *,
        actor: str,
        evidence: Mapping[str, object] | None,
    ) -> PolicyCell:
        provider = str(provider or "").strip()
        workload_family = str(workload_family or "").strip()
        if not provider or not workload_family:
            raise ValueError("a cell needs a provider and a load family")
        if not str(actor or "").strip():
            raise ValueError("a cell change names who made it")
        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"harness_policy_cell:{provider}/{workload_family}",),
            )
            row = connection.execute(
                "SELECT * FROM harness_policy_cells WHERE provider=%s AND workload_family=%s FOR UPDATE",
                (provider, workload_family),
            ).fetchone()
            current = _cell_from_row(row) if row else PolicyCell(provider, workload_family)
            updated, kind = change(current)
            updated = _replace(updated, updated_at=time.time())
            # The event names the challenger it is about: the new one, or the
            # one a promotion or retirement ended.
            involved = updated.challenger if updated.challenger is not None else current.challenger
            connection.execute(
                """
                INSERT INTO harness_policy_cells (
                    provider, workload_family, version, status, champion_json,
                    challenger_json, challenger_share, challenger_since,
                    evaluation_json, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (provider, workload_family) DO UPDATE SET
                    version=excluded.version, status=excluded.status,
                    champion_json=excluded.champion_json,
                    challenger_json=excluded.challenger_json,
                    challenger_share=excluded.challenger_share,
                    challenger_since=excluded.challenger_since,
                    evaluation_json=excluded.evaluation_json,
                    updated_at=excluded.updated_at
                """,
                (
                    provider, workload_family, updated.version, updated.status,
                    _json(updated.champion), _json(updated.challenger),
                    updated.challenger_share, instant(updated.challenger_since) if updated.challenger_since else None,
                    _json(updated.evaluation), instant(updated.updated_at),
                ),
            )
            connection.execute(
                """
                INSERT INTO harness_policy_events (
                    provider, workload_family, kind, version, champion_json,
                    challenger_json, evidence_json, actor, recorded_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    provider, workload_family, kind, updated.version,
                    _json(updated.champion), _json(involved),
                    _json(dict(evidence or {})), str(actor), instant(updated.updated_at),
                ),
            )
            connection.commit()
        PolicyCellCache.shared().invalidate()
        return updated

    # Turn calls ----------------------------------------------------------

    def write_turn_calls(self, turns: list[Any], *, keep_since: float) -> int:
        """Keep the calls of ``turns`` (``TurnCalls``); drop those older than ``keep_since``."""

        with self._connect() as connection:
            for turn in turns:
                connection.execute(
                    """
                    INSERT INTO harness_turn_calls (
                        execution_id, agent_id, provider, cell, arm, version,
                        recorded_at, compaction_json, calls_json, compactions_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (execution_id) DO NOTHING
                    """,
                    (
                        turn.execution_id, turn.agent_id, turn.provider, turn.cell,
                        turn.arm, int(turn.version), instant(turn.recorded_at),
                        _json(dict(turn.compaction)),
                        _json([list(call) for call in turn.calls]),
                        _json(list(turn.compactions)),
                    ),
                )
            removed = connection.execute(
                "DELETE FROM harness_turn_calls WHERE recorded_at < %s",
                (instant(keep_since),),
            ).rowcount
            connection.commit()
        return int(removed or 0)

    def turn_calls(self, *, since: float, until: float | None = None) -> list[dict[str, Any]]:
        clauses = ["recorded_at >= %s"]
        values: list[object] = [instant(since)]
        if until is not None:
            clauses.append("recorded_at < %s")
            values.append(instant(until))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM harness_turn_calls WHERE " + " AND ".join(clauses)
                + " ORDER BY recorded_at",
                values,
            ).fetchall()
        return [
            {
                "execution_id": str(row["execution_id"]),
                "agent_id": str(row["agent_id"]),
                "provider": str(row["provider"]),
                "cell": str(row["cell"]),
                "arm": str(row["arm"]),
                "version": int(row["version"]),
                "recorded_at": epoch_seconds(row["recorded_at"]),
                "compaction": _decode(row["compaction_json"]) or {},
                "calls": _decode(row["calls_json"]) or [],
                "compactions": _decode(row["compactions_json"]) or [],
            }
            for row in rows
        ]

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        with self.database.connect() as connection:
            if not self._initialized:
                with self._init_lock:
                    if not self._initialized:
                        connection.executescript(_SCHEMA_SQL)
                        connection.commit()
                        self._initialized = True
            yield connection


class PolicyCellCache:
    """The cells as one process last read them, at most a minute old.

    A failed refresh keeps the last copy; with none, every turn runs the
    Backend's parameters.  Choosing an arm therefore never waits for the
    database and never fails a turn.
    """

    _instance: "PolicyCellCache | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, store: HarnessEvolutionStore | None = None, *, refresh_seconds: float = CELL_REFRESH_SECONDS) -> None:
        self._store = store or HarnessEvolutionStore()
        self._refresh_lock = threading.Lock()
        self._refreshing = False
        self._refresh_seconds = float(refresh_seconds)
        self._lock = threading.Lock()
        self._cells: dict[tuple[str, str], PolicyCell] = {}
        self._loaded_at = 0.0

    @classmethod
    def shared(cls) -> "PolicyCellCache":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def invalidate(self) -> None:
        with self._lock:
            self._loaded_at = 0.0

    def lookup(self, provider: str, workload_family: str) -> PolicyCell | None:
        with self._lock:
            due = not self._loaded_at or time.monotonic() - self._loaded_at >= self._refresh_seconds
            if due and not self._refreshing:
                self._refreshing = True
                self._loaded_at = time.monotonic()
                threading.Thread(target=self.refresh, name="harness-policy-refresh", daemon=True).start()
            return self._cells.get((provider, workload_family))

    def refresh(self) -> None:
        """Refresh outside the turn thread, preserving the last copy on failure."""

        with self._refresh_lock:
            try:
                cells = {(cell.provider, cell.workload_family): cell for cell in self._store.cells()}
            except (OSError, DatabaseError, RuntimeError, ValueError):
                return
            else:
                with self._lock:
                    self._cells = cells
            finally:
                with self._lock:
                    self._loaded_at = time.monotonic()
                    self._refreshing = False


def _replace(cell: PolicyCell, **changes: Any) -> PolicyCell:
    return dataclasses.replace(cell, **changes)


def _cell_from_row(row: Mapping[str, Any]) -> PolicyCell:
    challenger = _decode(row["challenger_json"])
    return PolicyCell(
        provider=str(row["provider"]),
        workload_family=str(row["workload_family"]),
        version=int(row["version"]),
        status=str(row["status"]) if str(row["status"]) in CELL_STATUSES else STABLE,
        champion=_decode(row["champion_json"]) or {},
        challenger=challenger if isinstance(challenger, dict) else None,
        challenger_share=float(row["challenger_share"] or 0.0),
        challenger_since=epoch_seconds(row["challenger_since"]) or 0.0,
        evaluation=_decode(row["evaluation_json"]) or {},
        updated_at=epoch_seconds(row["updated_at"]) or 0.0,
    )


def _decode(value: object) -> Any:
    """A stored JSON value; a challenger left empty in older rows is none."""
    if not isinstance(value, str):
        return value
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _json(value: object) -> str:
    """JSON text of ``value``; no challenger is stored as JSON ``null``."""
    return json_text(value, ensure_ascii=False, sort_keys=True, default=str)
