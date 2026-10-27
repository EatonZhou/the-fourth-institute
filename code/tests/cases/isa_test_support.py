"""Run HATG ISA v2 calls in process for tests (routing mode ``local``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from management_portal.hatg import kernel as kernel_module, routing
from management_portal.hatg.contracts import InstructionContext, InstructionResult
from management_portal.society_service import resident_domain_common, resident_intake_domain


ACTOR = "sample_admin"


def reset_kernels() -> None:
    """Forget kernels built for earlier tests, so each test sees its own stores."""

    kernel_module.reset_kernels_for_tests()


def run(
    instruction: str,
    arguments: dict[str, Any] | None = None,
    *,
    actor: str = ACTOR,
    key: str = "",
    version: Any = None,
    entry: str = "web",
) -> InstructionResult:
    call: dict[str, Any] = {"instruction": instruction, "arguments": dict(arguments or {})}
    if key:
        call["idempotency_key"] = key
    if version is not None:
        call["expected_version"] = version
    return routing.execute(call, InstructionContext(caller=actor, entry=entry))


def ok(instruction: str, arguments: dict[str, Any] | None = None, **options: Any) -> Any:
    """The result of a call that must commit; raises its instruction error otherwise."""

    return run(instruction, arguments, **options).raise_for_error()


@pytest.fixture()
def isolated_instruction_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Lineage folders, Resident staging and resource boundary under ``tmp_path``, with fresh kernels."""

    from management_portal.society_service import (
        lineage_domain,
        resident_project_domain,
        resident_workspace_domain,
    )
    from tests.cases.society_service_test_support import install_resource_boundary

    root = tmp_path / "lineages"
    monkeypatch.setattr(lineage_domain, "DATA_ROOT", root)
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(root / "lineages"))
    install_resource_boundary(monkeypatch, synthetic_residents=False)
    monkeypatch.setattr(resident_domain_common, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(
        resident_intake_domain,
        "queue_bootstrap_resident_session",
        lambda *_args, **_kwargs: {"job_id": "bootstrap_test", "status": "queued"},
    )
    reset_kernels()
    yield root
    reset_kernels()


def publish_test_members(lineage: dict[str, Any], actor: str = ACTOR) -> None:
    """A graph-only fixture needs independent canonical identity facts for its nodes."""
    from tests.cases.society_service_test_support import publish_registered_lineage_members

    publish_registered_lineage_members(lineage, actor)
