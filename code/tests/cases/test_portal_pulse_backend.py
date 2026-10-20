from management_common.portal_database import portal_database
from management_portal import portal_pulse_backend as pulse
import json


def test_publications_are_rows_of_the_pulse_publications_table(tmp_path, monkeypatch) -> None:
    """Pulse 发布记录是 portal_pulse.publications 表的行

    发布与撤回 Pulse 条目只读写 PostgreSQL 的 portal_pulse.publications 表；摘要与 index.json 仍是 Pulse Lineage 的文件，遗留的 publications.json 既不被读取也不被改写。

    :module: platform.data
    """
    # Summaries stay the Pulse Resident's files; what users publish from them
    # is a table row, and a publications.json left in the folder is ignored.
    monkeypatch.setenv("PORTAL_PULSE_ROOT", str(tmp_path))
    evidence_path = str(pulse.portal_path("share_root") / "Projects" / "demo" / "notes.md")
    summary = tmp_path / "summaries" / "daily" / "2026-10-12.json"
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps({"period": "daily", "key": "2026-10-12", "content": {"user_private_updates": {
        "alice": {"items": [{"id": "one", "text": "Alice item", "project": "AI员工",
                             "evidence_paths": [evidence_path]}]},
    }}}), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps({"latest": {"daily": "2026-10-12"}}), encoding="utf-8")
    retired = tmp_path / "publications.json"
    retired.write_text(json.dumps({"schema": "tfi.portal_pulse.publications.v1", "items": [
        {"publication_id": "pub_from_file", "owner": "alice", "source_period": "daily", "source_key": "2026-10-12",
         "source_item_id": "one", "text": "x", "project": "", "evidence_paths": [], "published_at": "2026-10-12T00:00:00Z"},
    ]}), encoding="utf-8")
    before = retired.read_bytes()
    assert pulse._read_publications() == {"items": []}

    result = pulse.update_publication("alice", {"source_period": "daily", "source_key": "2026-10-12", "item_id": "one"})
    publication = result["publication"]
    assert publication["evidence_paths"] == [evidence_path]
    with portal_database(schema="portal_pulse").read() as connection:
        from management_common.database_time import decode_times
        rows = list(connection.execute("SELECT * FROM publications"))
        assert rows[0]['published_at'].tzinfo is not None
        assert [decode_times(row, ('published_at',)) for row in rows] == [publication]
    assert [item["publication_id"] for item in result["publications"]] == [publication["publication_id"]]
    assert result["periods"]["daily"]["mine"]["items"][0]["published"] is True

    pulse.update_publication("alice", {"action": "unpublish", "source_period": "daily", "source_key": "2026-10-12", "item_id": "one"})
    assert pulse._read_publications() == {"items": []}
    assert retired.read_bytes() == before


def test_every_caller_reads_the_public_pulse_and_only_their_own_items(monkeypatch, tmp_path):
    """每个调用者都能读到公共 Pulse，私人摘要只含自己的条目，负载不带可见性设置"""
    # Who may call GetPulse at all is the permission system's decision; the payload
    # itself keeps no user list or public switch.
    monkeypatch.setattr(pulse, '_read_publications', lambda: [{'publication_id': 'public-1'}])
    monkeypatch.setattr(pulse, '_load_latest_summaries', lambda: {'daily': {'id': 'daily-1'}})
    monkeypatch.setattr(pulse, '_public_summary', lambda summary: {'global_summary': 'public'})
    monkeypatch.setattr(pulse, '_private_summary', lambda summary, actor, publications: {'summary': f'private for {actor}', 'items': []})
    monkeypatch.setattr(pulse, '_published_items', lambda publications: [{'publication_id': 'public-1'}])
    monkeypatch.setattr(pulse, '_read_json', lambda path, default: default)
    monkeypatch.setenv('PORTAL_PULSE_ROOT', str(tmp_path))
    for actor in ('sample_reader', 'CASE-A'):
        payload = pulse.get_home_payload(actor)
        assert payload['enabled'] is True
        assert 'visibility' not in payload
        assert payload['periods']['daily']['public']['global_summary'] == 'public'
        assert payload['periods']['daily']['mine']['summary'] == f'private for {actor}'
        assert payload['publications'] == [{'publication_id': 'public-1'}]
