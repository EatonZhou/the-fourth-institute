from __future__ import annotations

import os
import uuid

import psycopg

from management_portal.execution import scheduler as scheduler_module
from management_portal.execution.contracts import ExecutionRequest


def test_shared_scheduler_trigger_handles_all_row_shapes_and_suppresses_only_worker_heartbeats(monkeypatch):
    """调度表的增删改都触发调度变更通知，只有 Worker 心跳更新不通知"""
    channel = "scheduler_test_" + uuid.uuid4().hex
    monkeypatch.setattr(scheduler_module, "SCHEDULER_CHANGE_CHANNEL", channel)
    scheduler = scheduler_module.Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    request = ExecutionRequest.new(actor="trigger-test", prompt="test", provider="test-api",
                                   service_type="chat_completion", operation="generate")
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"], autocommit=True) as listener:
        listener.execute("LISTEN " + channel)
        scheduler.submit(request)
        assert next(listener.notifies(timeout=1, stop_after=1), None) is not None
        with scheduler._connect() as connection:
            connection.execute("UPDATE executions SET updated_at=updated_at WHERE execution_id=%s", (request.execution_id,))
            connection.execute("INSERT INTO capacity_dispatch_state VALUES (%s, to_timestamp(0), to_timestamp(0), 0, %s)", ("trigger-probe", "now"))
            connection.execute("UPDATE capacity_dispatch_state SET interval_seconds=1 WHERE capacity_key=%s", ("trigger-probe",))
            connection.execute("DELETE FROM capacity_dispatch_state WHERE capacity_key=%s", ("trigger-probe",))
            connection.execute("INSERT INTO execution_input_controls VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               ("trigger-control", request.execution_id, "trigger-test", "{}", "", "pending", "", "now", "now"))
            connection.execute("UPDATE execution_input_controls SET status='completed' WHERE control_id=%s", ("trigger-control",))
            connection.execute("DELETE FROM execution_input_controls WHERE control_id=%s", ("trigger-control",))
            connection.execute("INSERT INTO execution_store_meta VALUES (%s,%s)", ("trigger-probe", "one"))
            connection.execute("UPDATE execution_store_meta SET value='two' WHERE key=%s", ("trigger-probe",))
            connection.execute("DELETE FROM execution_store_meta WHERE key=%s", ("trigger-probe",))
            connection.execute("DELETE FROM executions WHERE execution_id=%s", (request.execution_id,))
            connection.commit()
        assert next(listener.notifies(timeout=1, stop_after=1), None) is not None
        scheduler.register_worker(worker_id="trigger-test", build_id="test", capabilities=["chat_completion"])
        assert next(listener.notifies(timeout=1, stop_after=1), None) is not None
        with scheduler._connect() as connection:
            connection.execute("UPDATE execution_workers SET heartbeat_at=heartbeat_at + INTERVAL '1 second' WHERE worker_id=%s", ("trigger-test",))
            connection.commit()
        assert next(listener.notifies(timeout=0.1, stop_after=1), None) is None
        with scheduler._connect() as connection:
            connection.execute("UPDATE execution_workers SET state='draining' WHERE worker_id=%s", ("trigger-test",))
            connection.commit()
        assert next(listener.notifies(timeout=1, stop_after=1), None) is not None
        with scheduler._connect() as connection:
            connection.execute("DELETE FROM execution_workers WHERE worker_id=%s", ("trigger-test",))
            connection.commit()
        assert next(listener.notifies(timeout=1, stop_after=1), None) is not None


def test_a_restart_during_a_backup_does_not_wait_for_the_backups_locks():
    """备份期间重启调度器不等待备份持有的表锁"""
    # A pg_dump holds a shared lock on every table for the whole dump.
    # Startup dropped and recreated the triggers, whose exclusive lock waited
    # for the dump until the lock timeout, and the service restarted in a loop.
    import threading

    scheduler_module.Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    backup = scheduler_module.Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    errors: list[BaseException] = []

    def restart():
        try:
            scheduler_module.Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    with backup._connect() as reader:
        for table in ("executions", "execution_workers", "capacity_dispatch_state",
                      "execution_input_controls", "execution_store_meta"):
            reader.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchall()
        started = threading.Thread(target=restart)
        started.start()
        started.join(5)
        finished = not started.is_alive()
        reader.rollback()
    started.join(30)
    assert finished and not errors, "startup waited for a reader's table locks"
