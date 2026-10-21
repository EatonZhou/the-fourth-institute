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
def test_secret_write_failure_is_safe_and_rolls_back(monkeypatch, updating):
    api_id = "storage_failure_fixture"
    if updating:
        model_api_store.create_api({"id": api_id, "default_model": "original-model"}, "fixture")

    def fail_write(*args):
        raise PermissionError("synthetic-credential-must-not-escape")

    monkeypatch.setattr(account_hosting, "write_secret_file", fail_write)
    changes = {"secret": "synthetic-credential-must-not-escape", "default_model": "new-model"}
    args = {"api_id": api_id, **({"changes": changes} if updating else changes)}
    invocation = SimpleNamespace(caller="fixture", argument=lambda key, default=None: args.get(key, default))
    with pytest.raises(InstructionError) as caught:
        (isa.update_model_api if updating else isa.create_model_api)(invocation)
    assert caught.value.details["reason"] == "model_api.secret_write_failed"
    assert "synthetic-credential" not in str(caught.value)
    stored = dict((entry["id"], (entry, version)) for entry, version in model_api_store.apis())
    if updating:
        assert stored[api_id][0]["default_model"] == "original-model"
        assert stored[api_id][1] == 1
    else:
        assert api_id not in stored
