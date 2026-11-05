"""Credential storage failures remain actionable without exposing credentials.

:module: execution.providers
:importance: P1
"""
from types import SimpleNamespace

import pytest

from management_portal import model_api_store
from management_portal.execution import account_hosting, isa
from management_portal.hatg.contracts import InstructionError


@pytest.mark.parametrize("updating", [False, True])
def test_secret_write_failure_is_safe_and_rolls_back(monkeypatch, tmp_path, updating):
    from management_common.portal_database import portal_database
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.scheduler_schema import initialize_execution_store
    from management_portal.execution.account_registrations import registrations
    from tests.cases.instance_test_support import select_instance

    secrets = select_instance(monkeypatch, tmp_path / "instance").directory("secrets")
    get_agent_data_store()
    portal_database(schema="execution_runtime").write(initialize_execution_store)
    monkeypatch.setattr("management_portal.execution.provider_updates.runtime_available", lambda provider: False)
    api_id = "storage_failure_fixture"
    if updating:
        # Create through the real instruction, including protocol defaults and
        # associated accounts; an incomplete raw row fails validation before
        # the secret writer and cannot exercise rollback.
        initial = {"api_id": api_id, "base_url": "https://storage.invalid/v1",
                   "default_model": "original-model", "secret": "original-fixture-secret"}
        isa.create_model_api(SimpleNamespace(caller="fixture", argument=initial.get))
    before = model_api_store.apis()
    accounts_before = registrations()
    attempts = []

    def fail_write(*args):
        attempts.append(args[0])
        raise PermissionError("synthetic-credential-must-not-escape")

    monkeypatch.setattr(account_hosting, "write_secret_file", fail_write)
    changes = {"secret": "synthetic-credential-must-not-escape", "default_model": "new-model"}
    args = {"api_id": api_id, **({"changes": changes} if updating else changes)}
    invocation = SimpleNamespace(caller="fixture", argument=lambda key, default=None: args.get(key, default))
    with pytest.raises(InstructionError) as caught:
        (isa.update_model_api if updating else isa.create_model_api)(invocation)
    assert caught.value.details["reason"] == "model_api.secret_write_failed"
    assert "synthetic-credential" not in str(caught.value)
    assert attempts == [api_id]
    assert model_api_store.apis() == before
    assert registrations() == accounts_before
    stored = dict((entry["id"], (entry, version)) for entry, version in model_api_store.apis())
    if updating:
        assert stored[api_id][0]["default_model"] == "original-model"
        assert stored[api_id][1] == 1
        assert (secrets / f"{api_id}.key").read_text() == "original-fixture-secret"
    else:
        assert api_id not in stored
        assert not (secrets / f"{api_id}.key").exists()
