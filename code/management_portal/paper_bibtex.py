"""Read-only, deterministic BibTeX export from the registered manifest owner.

HTTP and authenticated script adapters must pass their verified principal to
export_bibtex. No adapter may accept a caller-supplied actor field.
"""
from __future__ import annotations

import re

from management_portal.paper_favorites import favorite_ids
from management_portal.paper_store import PAPER_STORE
from management_portal.resources.access import ResourceAccessPolicy


_ESCAPES = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
            "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
            "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _escape(value: object) -> str:
    return "".join(_ESCAPES.get(character, character) for character in _text(value))


def serialize_manifest(manifest: dict) -> dict:
    """Serialize one complete manifest without inferring missing bibliographic facts."""
    paper_id = str(manifest.get("paper_id") or "")
    if not re.fullmatch(r"paper_[0-9a-f]{12}", paper_id):
        raise ValueError("valid manifest paper_id required")
    warnings = []
    def warn(code, field):
        warnings.append({"paper_id": paper_id, "code": code, "field": field})
    kind = _text(manifest.get("document_type") or manifest.get("type") or "paper").lower()
    publication = _text(manifest.get("publication_type")).lower()
    entry_type = "book" if kind == "book" else "misc"
    if kind in {"paper", "article"}:
        entry_type = {"journal": "article", "conference": "inproceedings"}.get(publication, "misc")
        if not publication:
            warn("missing", "publication_type")
    elif kind not in {"book", "presentation", "slides", "other", "document"}:
        warn("unsupported_type", "document_type")
    fields = {}
    for key in ("title", "year", "publisher", "edition", "volume", "pages", "url"):
        if _text(manifest.get(key)):
            fields[key] = _escape(manifest[key])
    for source, target in (("issue", "number"), ("place", "address")):
        if _text(manifest.get(source)):
            fields[target] = _escape(manifest[source])
    # Preserve capitalization of the source title without interpreting source TeX.
    if "title" in fields:
        fields["title"] = "{" + fields["title"] + "}"
    authors = manifest.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
        warn("ambiguous_author_string", "authors")
    names = []
    for author in authors if isinstance(authors, list) else []:
        if isinstance(author, str) and _text(author):
            # The existing manifest stores display names, with no family/given
            # distinction. Preserve the entire name; do not guess a surname.
            names.append("{" + _escape(author) + "}")
        elif isinstance(author, dict):
            if _text(author.get("literal")):
                names.append("{" + _escape(author["literal"]) + "}")
            elif _text(author.get("family")):
                name = _escape(author["family"])
                if _text(author.get("given")):
                    name += ", " + _escape(author["given"])
                names.append(name)
            else:
                warn("invalid_author", "authors")
        else:
            warn("invalid_author", "authors")
    if names:
        fields["author"] = " and ".join(names)
    venue = _text(manifest.get("venue"))
    if venue and venue != "Unsorted":
        fields[{"article": "journal", "inproceedings": "booktitle"}.get(entry_type, "howpublished")] = _escape(venue)
    identifiers = manifest.get("identifiers") or {}
    if not isinstance(identifiers, dict):
        identifiers = {}
    for key in ("doi", "isbn", "issn"):
        if _text(identifiers.get(key)):
            fields[key] = _escape(identifiers[key])
    if _text(identifiers.get("arxiv")):
        fields["eprint"] = _escape(identifiers["arxiv"])
        fields["archivePrefix"] = "arXiv"
    source = manifest.get("source") or {}
    if "url" not in fields and isinstance(source, dict) and _text(source.get("pdf_url")):
        fields["url"] = _escape(source["pdf_url"])
    required = {"title", "author", "year"}
    required.update({"article": {"journal"}, "inproceedings": {"booktitle"}, "book": {"publisher"}}.get(entry_type, set()))
    for field in sorted(required - fields.keys()):
        warn("missing", field)
    lines = [f"@{entry_type}{{{paper_id},"]
    lines.extend(f"  {key} = {{{fields[key]}}}," for key in sorted(fields))
    return {"bibtex": "\n".join([*lines, "}", ""]), "warnings": warnings, "paper_id": paper_id}


def export_request(payload: dict, *, actor: str) -> dict:
    """Shared transport contract: identity is supplied only by authentication."""
    if not isinstance(payload, dict) or set(payload) - {"paper_ids", "favorites"}:
        raise ValueError("only paper_ids or favorites are accepted")
    return export_bibtex(actor=actor, **payload)


def export_bibtex(*, actor: str, paper_ids: list[str] | None = None, favorites: bool = False) -> dict:
    """Export explicit IDs or the verified principal's favorites, with no writes.

    Missing and inaccessible records share an unavailable warning to avoid
    revealing resource existence. Only authorized registered manifests are read.
    """
    if not str(actor or "").strip():
        raise PermissionError("authenticated principal required")
    if type(favorites) is not bool or (paper_ids is None) == (not favorites):
        raise ValueError("select exactly one of paper_ids or favorites")
    selected = favorite_ids(actor) if favorites else paper_ids
    if not isinstance(selected, list) or any(not isinstance(value, str) for value in selected):
        raise ValueError("paper_ids must be a list of strings")
    identifiers = list(dict.fromkeys(selected))
    if any(not re.fullmatch(r"paper_[0-9a-f]{12}", value) for value in identifiers):
        raise ValueError("valid paper_ids required")
    readable = ResourceAccessPolicy().readable_ids(identifiers, actor=actor) if identifiers else set()
    entries, warnings, exported = [], [], []
    for paper_id in identifiers:
        found = PAPER_STORE.manifest_for_paper_id(paper_id, allow_scan=False, registered_only=True) if paper_id in readable else None
        if found is None:
            warnings.append({"paper_id": paper_id, "code": "unavailable"})
            continue
        result = serialize_manifest(found[1])
        entries.append(result["bibtex"])
        warnings.extend(result["warnings"])
        exported.append(paper_id)
    return {"bibtex": "\n".join(entries), "paper_ids": exported, "warnings": warnings}
