""":design: decision_71aa039e3211"""
import hashlib
import inspect
import re
import types
from copy import deepcopy

import pytest

from management_portal.society_service import lineage_primitives
from management_portal.society_service import lineage_program_runtime as runtime
from management_portal.society_service import program_work_storage as work
from management_portal.society_service.coordinator import LineageCoordinator
from management_portal.society_service.state_store import get_lineage_data_store


LINEAGE = "lineage_aabbccddeeff"
# RUNTIME_NORMALIZATION → digest of the rule's source (_rule_source).
RULE_DIGESTS = {
    1: "0afd888c110030c165a16ee6aab0245aebf49b7693a2ca61f45f150e1c5b25a0",
    2: "91f41a7bd136fc935075c044e77c55bcded4ebd382161c99685d35308418396b",
    3: "44faacdd2f32322e64a43d798b747fc0419de8bb4b84d69b5ad671046de7fa0d",
}


def _rule_source() -> str:
    """_normalize, every function of its two modules it reaches, and the constants they read."""
    modules = {module.__name__: module for module in (runtime, lineage_primitives)}
    seen, parts, queue = set(), set(), [runtime._normalize]
    while queue:
        function = queue.pop()
        # Named without the package, so moving the modules leaves the rule unchanged.
        name = f"{function.__module__.rsplit('.', 1)[-1]}.{function.__qualname__}"
        if name in seen:
            continue
        seen.add(name)
        parts.add(f"# {name}\n{inspect.getsource(function)}")
        codes, names = [function.__code__], set()
        while codes:
            code = codes.pop()
            names.update(code.co_names)
            codes.extend(const for const in code.co_consts if isinstance(const, types.CodeType))
        for scope in (function.__globals__, *(vars(module) for module in modules.values())):
            for key in sorted(names):
                value = scope.get(key)
                if isinstance(value, types.FunctionType) and value.__module__ in modules:
                    queue.append(value)
                elif key.isupper() and isinstance(value, (int, str)):
                    parts.add(f"{key} = {value!r}")
                elif key.isupper() and isinstance(value, frozenset):
                    parts.add(f"{key} = {sorted(value)!r}")
                elif isinstance(value, re.Pattern):
                    parts.add(f"{key} = re.compile({value.pattern!r})")
    return "\n".join(sorted(parts))


def test_runtime_normalization_rule_is_pinned_to_its_number():
    """运行时规范化规则变化时必须加版本号并迁移已存数据

    Stored runtime objects are read without normalizing once lineage_meta
    records that this rule wrote them. A changed rule therefore needs
    RUNTIME_NORMALIZATION + 1: the Release SocietyService then renormalizes
    the stored objects at its next start. Put the new digest under the new
    number here.
    """
    digest = hashlib.sha256(_rule_source().encode()).hexdigest()
    assert RULE_DIGESTS.get(runtime.RUNTIME_NORMALIZATION) == digest, (
        "运行时规范化规则变了：把 RUNTIME_NORMALIZATION 加一，并在 RULE_DIGESTS 记下新摘要 " + digest
    )


@pytest.fixture
def database(monkeypatch):
    store = get_lineage_data_store()
    store.put_lineage_definition(LINEAGE, {
        "lineage_id": LINEAGE, "title": "Runtime normalization",
        "nodes": [], "edges": [], "program": {},
    }, {"lineage_id": LINEAGE})
    monkeypatch.setattr(runtime, "_trust", (float("-inf"), False))
    return store.database


def test_reads_trust_stored_runtime_once_this_rule_migrated_it(database, monkeypatch):
    """运行时读取在本规则迁移过已存数据后直接返回已存对象

    Until migrate_stored_runtime has renormalized every stored object, reads
    normalize. After it, they return the stored objects as they are, and a
    second migration has nothing to do.
    """
    def recheck():
        monkeypatch.setattr(runtime, "_trust", (float("-inf"), False))

    # Written under an older rule: this one drops the repeated cursor.
    with work.transaction(database, LINEAGE) as state:
        state["event_sources"] = {"source": {"consumed_cursors": ["one", "one", "two"]}}
    database.write(lambda connection: connection.execute(
        "INSERT INTO lineage_meta(key, value) VALUES (%s, %s)",
        (runtime._NORMALIZATION_KEY, str(runtime.RUNTIME_NORMALIZATION - 1)),
    ))
    normalized = {"consumed_cursors": ["one", "two"]}
    assert runtime.read_runtime(LINEAGE)["event_sources"]["source"] == normalized
    assert runtime.migrate_stored_runtime() == {
        "normalization": runtime.RUNTIME_NORMALIZATION, "lineages": 1, "migrated": 1,
    }
    with database.read() as connection:
        assert work.read(connection, LINEAGE)["event_sources"]["source"] == normalized
        assert runtime._stored_normalization(connection) == runtime.RUNTIME_NORMALIZATION
    recheck()
    def refuse(*_args):
        pytest.fail("a trusted read normalized")
    monkeypatch.setattr(runtime, "_normalize", refuse)
    monkeypatch.setattr(runtime, "_normalize_object", refuse)
    assert runtime.read_runtime(LINEAGE) == {**runtime.empty_runtime(), "event_sources": {"source": normalized}}
    assert runtime._read_object(LINEAGE, "event_sources", "source") == normalized
    assert runtime.migrate_stored_runtime() == {
        "normalization": runtime.RUNTIME_NORMALIZATION, "lineages": 0, "migrated": 0,
    }


def test_reads_normalize_when_another_rule_wrote_the_stored_runtime(database, monkeypatch):
    """已存数据由别的规则版本写入时读取仍做规范化

    A Dev SocietyService running an unpublished rule finds the published
    number in lineage_meta, so it keeps normalizing what it reads.
    """
    held = {"claim_token": "claim_original", "cursor": "held_cursor",
            "recovery_blocked": {"reason": "manual_recovery"}, "opaque_history": {"keep": True}}
    expired = {"wire_id": "wire", "cursor": "expired_cursor",
               "recovery_blocked": {"reason": "manual_recovery"}, "opaque_history": ["keep"]}
    raw = {"event_sources": {"source": {"consumed_cursors": ["one", "one"]}},
           "event_wires": {"wire": {"pending_events": [held]}},
           "operators": {"operator": {"cursor": "original_checkpoint", "message_id": "msg_original"}},
           "delivery_profiles": {"msg_original": {"isolated": True}},
           "expired_deliveries": [expired]}
    with work.transaction(database, LINEAGE) as state:
        for field, value in raw.items():
            state[field] = deepcopy(value)
    database.write(lambda connection: connection.execute(
        "INSERT INTO lineage_meta(key, value) VALUES (%s, %s)",
        (runtime._NORMALIZATION_KEY, str(runtime.RUNTIME_NORMALIZATION - 1)),
    ))
    assert runtime.read_runtime(LINEAGE)["event_sources"]["source"] == {"consumed_cursors": ["one"]}
    assert runtime._read_object(LINEAGE, "event_sources", "source") == {"consumed_cursors": ["one"]}

    def row_keys(connection):
        return {table: list(connection.execute(
            "SELECT " + ",".join(columns[:-1]) + " FROM " + table + " ORDER BY 1,2"
        ).fetchall()) for table, columns in work.COLUMNS.items()}

    with database.read() as connection:
        assert work.read(connection, LINEAGE) == raw  # Reads never rewrite old records.
        before_keys = row_keys(connection)
    normalize = runtime._normalize_object
    def fail_after_source(field, key, value):
        if field == "operators":
            raise RuntimeError("injected migration failure")
        return normalize(field, key, value)
    with monkeypatch.context() as patched:
        patched.setattr(runtime, "_normalize_object", fail_after_source)
        with pytest.raises(RuntimeError, match="injected migration failure"):
            runtime.migrate_stored_runtime()
    with database.read() as connection:
        assert runtime._stored_normalization(connection) == runtime.RUNTIME_NORMALIZATION - 1
        assert work.read(connection, LINEAGE) == raw
        assert row_keys(connection) == before_keys

    assert runtime.migrate_stored_runtime()["normalization"] == runtime.RUNTIME_NORMALIZATION
    with database.read() as connection:
        assert runtime._stored_normalization(connection) == runtime.RUNTIME_NORMALIZATION
        assert work.read(connection, LINEAGE) == {
            **raw, "event_sources": {"source": {"consumed_cursors": ["one"]}},
        }
        assert row_keys(connection) == before_keys


def test_the_coordinator_records_the_runtime_migration_it_ran_at_start():
    """协调器启动时迁移一次已存运行时并记录结果"""
    calls = []
    coordinator = LineageCoordinator(
        resume_residents=lambda: {}, resume_resident=lambda _: {}, reconcile_all=lambda: {},
        reconcile_lineage=lambda _: {},
        migrate_runtime=lambda: calls.append(1) or {"normalization": 1, "lineages": 3, "migrated": 2},
    )
    coordinator._migrate_stored_runtime()
    assert calls == [1]
    assert coordinator.snapshot()["last_runtime_migration"]["migrated"] == 2
