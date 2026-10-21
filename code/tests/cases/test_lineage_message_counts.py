""":design: decision_71aa039e3211"""
from management_portal.society_service import lineage_domain


def test_slow_message_counts_do_not_block_other_lineages_or_definition_cache(monkeypatch):
    """消息计数计算不阻塞其他团队的读取

    验证同范围计算合并、不同团队与定义缓存可独立读取。

    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from management_portal.society_service.lineage_read_cache import LineageReadCache

    cache = LineageReadCache()
    monkeypatch.setattr(lineage_domain, "_LINEAGE_READ_CACHE", cache)
    monkeypatch.setattr(lineage_domain, "_lineage_read_cache_key", lambda lid, actor: ("test", lid, actor))
    monkeypatch.setattr(lineage_domain, "get_lineage", lambda lid, actor: {"lineage_id": lid, "revision": 1, "nodes": []})
    monkeypatch.setattr(lineage_domain.resident_protocol, "read_resident_state_snapshots", lambda ids: {})
    entered, release = Event(), Event()
    reads = []

    def build(lid, actor, lineage):
        reads.append(lid)
        if lid == "slow":
            entered.set()
            assert release.wait(5)
        return {"lineage_id": lid, "sessions": []}

    monkeypatch.setattr(lineage_domain, "_build_node_message_counts_payload", build)
    with ThreadPoolExecutor(max_workers=4) as pool:
        slow = pool.submit(lineage_domain.get_node_message_counts, "slow", "viewer")
        try:
            assert entered.wait(2)
            same = pool.submit(lineage_domain.get_node_message_counts, "slow", "viewer")
            unrelated = pool.submit(lineage_domain.get_node_message_counts, "other", "viewer")
            definition = pool.submit(cache.get, "view", ("test", "slow", "viewer"), 1)
            assert unrelated.result(timeout=1)["lineage_id"] == "other"
            assert definition.result(timeout=1) is None
        finally:
            release.set()
        assert slow.result() == same.result()
    assert reads.count("slow") == 1


def test_node_message_count_cache_tracks_conversation_cursor(monkeypatch):
    """消息计数缓存随对话游标变化重新计算

    :design-point:"""
    lineage_id = "lineage_0123456789ab"
    resident_id = "resident_0123456789ab"
    lineage = {
        "lineage_id": lineage_id,
        "revision": 7,
        "nodes": [{"id": resident_id}],
    }
    cursor = {resident_id: {"cursor": 1}}
    counts = {resident_id: 0}
    reads = []

    monkeypatch.setattr(lineage_domain, "get_lineage", lambda *_: lineage)
    monkeypatch.setattr(
        lineage_domain.resident_protocol,
        "read_resident_state_snapshots",
        lambda _resident_ids: cursor,
    )

    def message_counts(_resident_ids, _actor):
        reads.append(dict(counts))
        return dict(counts)

    monkeypatch.setattr(
        lineage_domain.resident_domain,
        "get_resident_user_message_counts",
        message_counts,
    )
    monkeypatch.setattr(
        lineage_domain.resident_domain,
        "get_resident_context_pressures",
        lambda *_: {},
    )
    lineage_domain._LINEAGE_READ_CACHE.clear("message_counts")

    first = lineage_domain.get_node_message_counts(lineage_id, "sample_admin")
    assert first["sessions"][0]["user_message_count"] == 0
    assert len(reads) == 1
    assert lineage_domain.get_node_message_counts(lineage_id, "sample_admin") == first
    assert len(reads) == 1

    counts[resident_id] = 1
    cursor[resident_id] = {"cursor": 2}
    updated = lineage_domain.get_node_message_counts(lineage_id, "sample_admin")
    assert updated["sessions"][0]["user_message_count"] == 1
    assert len(reads) == 2
