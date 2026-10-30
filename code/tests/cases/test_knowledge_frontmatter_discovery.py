"""Knowledge discovery and version reads share the same YAML boundary."""
from __future__ import annotations

import pytest

from knowledge_board_lan import knowledge_source


@pytest.mark.parametrize(
    ("prefix", "newline", "padding"),
    [("", "\n", ""), ("\ufeff", "\n", ""), ("\ufeff", "\r\n", ""),
     ("", "\n", "# metadata comment\n" * 410)],
    ids=["plain", "utf8-bom", "bom-crlf", "long-valid-header"],
)
def test_discovery_keeps_identity_and_version_for_valid_yaml(tmp_path, prefix, newline, padding):
    text = prefix + (
        "---\nkb_id: decision_0123456789ab\ntype: decision\n"
        "version:\n  number: 9\n" + padding + "---\n\n# Evidence\n\nVisible body.\n"
    ).replace("\n", newline)
    path = tmp_path / "item.md"
    path.write_bytes(text.encode("utf-8"))
    original = (path.read_bytes(), path.stat().st_mtime_ns)
    assert knowledge_source.knowledge_version_record(text)["number"] == 9

    record = knowledge_source._read_kb_file(
        str(path), "Fixture", {"decision"}, {"decision": "decision"},
        entry_root=tmp_path, entry_files=[path],
    )

    assert record.get("kb_id") == "decision_0123456789ab"
    assert record["title"] == "Evidence"
    assert record["preview"] == "Visible body."
    assert record["version"]["number"] == 9
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original


@pytest.mark.parametrize("text", [
    "# Body\n---\nkb_id: decision_0123456789ab\n---\n",
    "---\nkb_id: decision_0123456789ab\ntype: decision\n",
    "---\n- decision_0123456789ab\n---\n# Body\n",
    "\ufeff---\nkb_id: [broken\ntype: decision\n---\n# Body\n",
])
def test_discovery_rejects_missing_or_malformed_yaml_identity(tmp_path, text):
    path = tmp_path / "item.md"
    path.write_text(text, encoding="utf-8")
    original = (path.read_bytes(), path.stat().st_mtime_ns)
    assert knowledge_source._read_kb_file(
        str(path), "Fixture", {"decision"}, {"decision": "decision"},
        entry_root=tmp_path, entry_files=[path],
    ) == {}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original
