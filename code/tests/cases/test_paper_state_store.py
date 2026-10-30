""":importance: P1"""
from __future__ import annotations

import threading
import time

import psycopg
import pytest
from psycopg.types.json import Jsonb

from management_portal import paper_ccf, paper_state_store


def _ids(kind: str, **filters) -> list[str]:
    id_field = paper_state_store.KINDS[kind][0]
    return [record[id_field] for record in paper_state_store.records(kind, **filters)]


def test_records_keep_their_first_stored_order_through_updates():
    """Paper 状态表保持记录首次写入的顺序

    评审、笔记和任务记录按首次写入的顺序读出。更新保留原位置，新 ID 追加到末尾；按论文过滤、按 ID 读取、删除和拒绝重复 ID 都与原 JSON 列表的语义一致。
    """
    paper_state_store.append("exp_jobs", {"job_id": "a", "paper_id": "p1", "status": "queued"})
    paper_state_store.append("exp_jobs", {"job_id": "b", "paper_id": "p2", "status": "queued"})
    assert paper_state_store.merge("exp_jobs", "a", {"status": "done"}) == {
        "job_id": "a", "paper_id": "p1", "status": "done",
    }
    assert paper_state_store.merge("exp_jobs", "c", {"paper_id": "p1"}) == {"job_id": "c", "paper_id": "p1"}
    assert _ids("exp_jobs") == ["a", "b", "c"]
    assert _ids("exp_jobs", paper_id="p1") == ["a", "c"]
    assert paper_state_store.get("exp_jobs", "b")["paper_id"] == "p2"
    assert paper_state_store.get("exp_jobs", "missing") is None
    with pytest.raises(psycopg.errors.UniqueViolation):
        paper_state_store.append("exp_jobs", {"job_id": "a"})
    assert paper_state_store.get("exp_jobs", "a")["status"] == "done"
    assert paper_state_store.delete("exp_jobs", "b")["job_id"] == "b"
    assert paper_state_store.delete("exp_jobs", "b") is None
    assert paper_state_store.delete_paper("exp_jobs", "p1") == 2
    assert paper_state_store.records("exp_jobs") == []


@pytest.mark.parametrize("kind", list(paper_state_store.KINDS))
def test_all_paper_collections_keep_more_than_one_thousand_records(kind):
    """论文各状态集合保留超过一千条记录及早期运行任务

    :importance: P2"""
    identity = paper_state_store.KINDS[kind][0]
    with paper_state_store.locked(kind):
        for index in range(1005):
            record = {identity: f"item-{index:04d}", "created_at": 1700000000 + index}
            if kind.endswith("_jobs"):
                record["status"] = "running" if index == 0 else "done"
            paper_state_store.append(kind, record)
    assert len(paper_state_store.records(kind)) == 1005
    first = paper_state_store.get(kind, "item-0000")
    assert first["created_at"] == 1700000000
    if kind.endswith("_jobs"):
        assert first["status"] == "running"


def test_typed_paper_fields_and_unknown_field_rejection():
    """论文状态字段采用数据库类型并拒绝未知字段修改

    :importance: P2"""
    paper_state_store.append("notes", {"note_id": "note-a", "paper_id": "p", "body": "preserve", "created_at": 1700000000})
    with paper_state_store.database().read() as connection:
        assert connection.execute("SELECT pg_typeof(created_at)::text FROM notes").fetchone()[0] == "timestamp with time zone"
    with pytest.raises(ValueError, match="Unknown Paper"):
        paper_state_store.merge("notes", "note-a", {"accidental_field": True})
    assert paper_state_store.get("notes", "note-a")["body"] == "preserve"


def test_locked_check_then_write_admits_one_writer_per_kind():
    """Paper 状态写锁让先查后写只放行一个写入者

    两个并发事务都在同一类记录的写锁内先检查、后追加，只有一个能写入；它取代原来的 .lock 文件锁，防止为同一篇论文重复创建任务。
    """
    ready = threading.Barrier(2)
    started: list[str] = []
    errors: list[BaseException] = []

    def start(job_id: str) -> None:
        try:
            ready.wait(5)
            with paper_state_store.locked("exp_jobs"):
                if not paper_state_store.records("exp_jobs", paper_id="p1"):
                    time.sleep(0.2)
                    paper_state_store.append("exp_jobs", {"job_id": job_id, "paper_id": "p1"})
                    started.append(job_id)
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=start, args=(job_id,)) for job_id in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert errors == []
    assert len(started) == 1
    assert _ids("exp_jobs") == started


def test_ccf_catalog_reads_curated_venues_from_the_database():
    """CCF 目录从数据库读取精选会议

    精选芯片/电路会议列表存放在 PostgreSQL 的 paper.curated_venues 表中；CCF 目录的 Curated 分组按存储顺序列出规范化后的会议，不再读取 curated_venues.json。
    """
    with paper_state_store.database().transaction() as connection:
        for venue in (
            {"dblp_conf_key": "dac", "abbr": "DAC", "name": "Design Automation Conference"},
            {"dblp_conf_key": "iccad", "abbr": "ICCAD", "category": "芯片电路"},
        ):
            connection.execute(
                "INSERT INTO curated_venues (dblp_conf_key, venue) VALUES (%s, %s)",
                (venue["dblp_conf_key"], Jsonb(venue)),
            )

    venues = paper_ccf.ccf_venues({"rank": "CURATED"})["venues"]

    assert [venue["dblp_conf_key"] for venue in venues] == ["dac", "iccad"]
    assert all(venue["rank"] == "Curated" and venue["curated"] for venue in venues)
    assert venues[0]["dblp_url"] == "https://dblp.org/db/conf/dac/"
