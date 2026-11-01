"""The folder of a registered Lineage, computed from its Project in the Lineage store."""
from __future__ import annotations

from pathlib import Path

from . import lineage_storage
from .state_store import get_lineage_data_store


def lineage_folder(lineage_id: object) -> Path:
    with get_lineage_data_store().database.read() as connection:
        return lineage_storage.lineage_folders([lineage_id], connection=connection)[
            lineage_storage._lineage_id(lineage_id)]
