"""An added Provider owns configuration, login and usage without new ISA branches."""

from types import SimpleNamespace

import pytest

from management_portal.execution import account_configuration, account_login, account_registrations, provider_usage_events
from management_portal.execution.provider_account_operations import AccountOperation
from management_portal.execution.provider_catalog import AGENT_PROVIDERS, AgentProvider
from management_portal.hatg.contracts import InstructionError


def fixture_configuration(row, *, changes=None, expected_version=None, include_references=False):
    return {"configuration": {"account_id": row["account_id"], "version": "v1", "dial": changes["dial"]},
            "changed": expected_version == "v1"}


def fixture_login(row, action, *, options=None, authorization_code=None):
    return {"session": {"state": "awaiting_user", "account_id": row["account_id"]},
            "option_values": options or {"channel": "browser"}}


def fixture_usage(row, **query):
    return {"account_id": row["account_id"], "records": [{"meter": query["limit"]}], "next_cursor": ""}


def object_schema(key, specification):
    return {"type": "object", "additionalProperties": False, "required": [key], "properties": {key: specification}}


def invocation(**arguments):
    return SimpleNamespace(argument=arguments.get, expected_version_for=lambda _: "v1")


def test_new_provider_uses_declared_account_operations(monkeypatch):
    monkeypatch.setitem(AGENT_PROVIDERS, "fixture", AgentProvider("fixture", "Fixture", frozenset(), (),
        account_operations={
            "configuration": AccountOperation(__name__ + ".fixture_configuration",
                schema=object_schema("dial", {"type": "integer", "minimum": 1})),
            "login": AccountOperation(__name__ + ".fixture_login", authorization_kind="browser_token",
                schema=object_schema("channel", {"enum": ["browser", "console"]})),
            "usage_events": AccountOperation(__name__ + ".fixture_usage"),
        }))
    monkeypatch.setattr(account_registrations, "registration", lambda account_id: {
        "account_id": account_id, "provider": "fixture", "kind": "subscription_login",
    })

    assert account_configuration.update_configuration(invocation(account_id="fixture_one", changes={"dial": 4})) == {
        "configuration": {"account_id": "fixture_one", "version": "v1", "dial": 4}, "changed": True,
    }
    login = account_login.open_provider_account_login(invocation(account_id="fixture_one", options={"channel": "console"}))
    assert login["login_options"]["values"] == {"channel": "console"}
    assert login["session"]["input_kind"] == "browser_token"
    assert account_login.get_provider_account_login(invocation(account_id="fixture_one"))["login_options"]["values"] == {
        "channel": "browser",
    }
    assert provider_usage_events.get_provider_usage_events(invocation(account_id="fixture_one", limit=3))["records"] == [
        {"meter": 3},
    ]
    with pytest.raises(InstructionError, match="changes"):
        account_configuration.update_configuration(invocation(account_id="fixture_one", changes={"dial": True}))
    with pytest.raises(InstructionError, match="options"):
        account_login.open_provider_account_login(invocation(account_id="fixture_one", options={"channel": "console", "region": "global"}))
    with pytest.raises(InstructionError) as exc:
        account_configuration.get_configuration(invocation(account_id="fixture_one", include_provider_references=True))
    assert exc.value.details == {"reason": "provider.capability_unsupported", "capability": "configuration_references"}
