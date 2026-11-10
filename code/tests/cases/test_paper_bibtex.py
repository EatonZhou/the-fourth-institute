"""Deterministic BibTeX from complete manifests; isolated registered selection."""
import copy
import json
from unittest import mock

import pytest

from management_portal import paper_bibtex, paper_store, presentation_store


ONE = "paper_000000000001"
TWO = "paper_000000000002"
THREE = "paper_000000000003"


def manifest(paper_id=ONE, **fields):
    return {"paper_id": paper_id, "title": "GPT 中文 Énergie & 50%_#{x} $ \\ ~ ^",
            "authors": ["张三", "Émilie du Châtelet", "Research and Development"],
            "year": "2026", "venue": "会议", "identifiers": {"doi": "10.123/a_b"}, **fields}


def test_serialization_is_deterministic_and_independently_parseable():
    # Test-only parser; production export has no parser/dependency requirement.
    bibtexparser = pytest.importorskip("bibtexparser")
    source = manifest()
    original = copy.deepcopy(source)
    first = paper_bibtex.serialize_manifest(source)
    assert first == paper_bibtex.serialize_manifest(source)
    assert source == original
    parsed = bibtexparser.loads(first["bibtex"]).entries
    assert len(parsed) == 1 and parsed[0]["ID"] == ONE
    assert parsed[0]["ENTRYTYPE"] == "misc"
    assert "中文" in parsed[0]["title"] and "Énergie" in parsed[0]["title"]
    assert r"\&" in parsed[0]["title"] and r"\%" in parsed[0]["title"]
    assert r"\_" in parsed[0]["doi"]
    assert "{Research and Development}" in parsed[0]["author"]
    changed = paper_bibtex.serialize_manifest({**source, "title": "New Title", "year": "2027"})
    assert bibtexparser.loads(changed["bibtex"]).entries[0]["ID"] == ONE


@pytest.mark.parametrize("fields,kind,container", [
    ({"publication_type": "journal"}, "article", "journal"),
    ({"publication_type": "conference"}, "inproceedings", "booktitle"),
    ({"document_type": "book", "publisher": "出版社"}, "book", "howpublished"),
    ({"document_type": "presentation"}, "misc", "howpublished"),
])
def test_explicit_document_types_keep_publication_information(fields, kind, container):
    result = paper_bibtex.serialize_manifest(manifest(**fields))
    assert result["bibtex"].startswith(f"@{kind}{{{ONE},")
    assert f"{container} = {{会议}}" in result["bibtex"]


def test_missing_fields_and_authors_are_not_invented():
    result = paper_bibtex.serialize_manifest({"paper_id": ONE, "document_type": "book"})
    assert {w["field"] for w in result["warnings"]} == {"title", "author", "year", "publisher"}
    result = paper_bibtex.serialize_manifest(manifest(authors=[
        {"family": "Doe", "given": "Jane"}, {"literal": "A & B Institute"}]))
    assert r"author = {Doe, Jane and {A \& B Institute}}" in result["bibtex"]
    ambiguous = paper_bibtex.serialize_manifest(manifest(authors="Doe, Jane; 张三"))
    assert any(w["code"] == "ambiguous_author_string" for w in ambiguous["warnings"])


def test_registered_selection_and_favorites_are_read_only_and_isolated(tmp_path, monkeypatch):
    store = paper_store.PaperStore(tmp_path)
    roots = {}
    for paper_id in (ONE, TWO):
        root = tmp_path / paper_id
        root.mkdir()
        roots[paper_id] = str(root)
        (root / "manifest.json").write_text(json.dumps(manifest(paper_id)), encoding="utf-8")
    monkeypatch.setattr(paper_bibtex, "PAPER_STORE", store)
    monkeypatch.setattr("management_common.instance_locations.located_path", lambda path: path)
    monkeypatch.setattr("management_portal.resources.directory_registrations.is_deleted", lambda value: False)
    monkeypatch.setattr(paper_bibtex.ResourceAccessPolicy, "readable_ids",
                        lambda self, ids, actor: set(ids) - ({TWO} if actor == "alice" else set()))
    presentation_store.set_favorite("alice", "paper", ONE, True)
    presentation_store.set_favorite("bob", "paper", TWO, True)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with mock.patch("management_portal.resources.registry_store.get_registry_store") as registry, \
         mock.patch("management_common.directory_cleanup_store.get_directory_cleanup_store") as cleanup, \
         mock.patch.object(store, "manifest_map", side_effect=AssertionError("legacy mapping")), \
         mock.patch.object(store, "manifest_paths", side_effect=AssertionError("scan")), \
         mock.patch.object(store, "write_manifest", side_effect=AssertionError("write")):
        registry.return_value.get.side_effect = lambda collection, identifier: (
            {"directories": [{"path": roots[identifier]}]} if identifier in roots else None)
        cleanup.return_value.pending_at.return_value = False
        result = paper_bibtex.export_bibtex(actor="alice", paper_ids=[ONE, TWO, THREE, ONE])
        assert result["paper_ids"] == [ONE]
        assert {w["paper_id"] for w in result["warnings"] if w["code"] == "unavailable"} == {TWO, THREE}
        assert result == paper_bibtex.export_bibtex(actor="alice", paper_ids=[ONE, TWO, THREE, ONE])
        assert paper_bibtex.export_bibtex(actor="alice", favorites=True)["paper_ids"] == [ONE]
        assert paper_bibtex.export_bibtex(actor="bob", favorites=True)["paper_ids"] == [TWO]
        assert paper_bibtex.export_bibtex(actor="bob", paper_ids=[])["bibtex"] == ""
        assert all(call.args[1] != TWO for call in registry.return_value.get.call_args_list[:2])
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_invalid_selector_and_anonymous_export_rejected():
    for options in ({}, {"paper_ids": [], "favorites": True}, {"paper_ids": "not-a-list"},
                    {"paper_ids": ["not-an-id"]}):
        with pytest.raises(ValueError):
            paper_bibtex.export_bibtex(actor="alice", **options)
    with pytest.raises(PermissionError):
        paper_bibtex.export_bibtex(actor="", paper_ids=[])


def test_manifest_values_are_complete_and_unsorted_is_not_a_publication():
    long_title = "完整标题" * 300
    result = paper_bibtex.serialize_manifest(manifest(title=long_title, venue="Unsorted", publication_type="journal"))
    assert long_title in result["bibtex"]
    assert "journal =" not in result["bibtex"]
    assert {"paper_id": ONE, "code": "missing", "field": "journal"} in result["warnings"]
