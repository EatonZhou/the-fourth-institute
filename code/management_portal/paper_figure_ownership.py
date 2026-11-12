"""Figure entry adapter: one file lock protects claim, metadata and publication."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from . import paper_job_ownership as ownership, paper_state_store

_documents = ContextVar("paper_figure_documents", default={})


class FigureAdapter:
    def __init__(self, paper_id, figure_id, generated_id=""):
        self.paper_id, self.figure_id, self.generated_id = paper_id, figure_id, generated_id
        self.identity = f"figure:{paper_id}:{figure_id}:{generated_id or 'explanation'}"
        self.kind = "figure"

    @property
    def document(self):
        return _documents.get()[self.paper_id]

    @contextmanager
    def locked(self):
        from .paper_figures import _load_figures, _write_figures, _figures_json_path
        from .paper_store import LOCK, _paper_by_id, _paper_job_store_lock
        if self.paper_id in _documents.get():
            with paper_state_store.database().read() as connection:
                yield connection
            return
        paper = _paper_by_id(self.paper_id)
        if not paper:
            raise KeyError("Paper not found")
        with LOCK, _paper_job_store_lock(_figures_json_path(paper)):
            token = _documents.set({**_documents.get(), self.paper_id: _load_figures(paper)})
            original = deepcopy(self.document)
            try:
                with paper_state_store.database().read() as connection:
                    yield connection
                if self.document != original:
                    _write_figures(paper, self.document)
            finally:
                _documents.reset(token)

    def entry(self):
        figure = next((row for row in self.document if row.get("figure_id") == self.figure_id), None)
        if not figure or not self.generated_id:
            return figure
        return next((row for row in figure.get("generated", []) if row.get("generated_id") == self.generated_id), None)

    def get(self, identity):
        if self.paper_id not in _documents.get():
            with self.locked():
                return self.get(identity)
        entry = self.entry()
        if entry is None:
            return None
        return {**entry, "status": entry.get("status" if self.generated_id else "explanation_status"),
                "resident_handoff": dict(entry.get("_job_execution") or {})}

    def merge(self, identity, patch):
        entry = self.entry()
        if entry is None:
            raise ownership.OwnershipLost("Figure entry was removed")
        for key, value in patch.items():
            target = "_job_execution" if key == "resident_handoff" else (
                key if self.generated_id or key not in {"status", "step", "error"} else "explanation_" + key)
            entry[target] = value
        return self.get(identity)


def current_adapter():
    owner = ownership._owner.get()
    return owner[2] if owner and len(owner) > 2 and isinstance(owner[2], FigureAdapter) else None
