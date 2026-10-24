"""Claude onboarding consumes an explicitly configured model catalog.

:module: execution.providers
:profiles: local.fixture
:risk: no-external-side-effects
"""
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from management_portal import claude_provider_catalog as catalog


@pytest.mark.parametrize("scenario", ["register", "repair", "conflict", "local-register", "local-other"])
def test_native_claude_catalog_ui(scenario):
    """Registration and repair preserve explicit models and registration CAS.

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run([
        "node", str(Path(__file__).with_name("javascript") / "claude_setup_catalog.cjs"),
        str(root / "management_portal/static/portal_management_backend.js"), scenario,
    ], cwd=root, check=True)


def native(**config):
    return {"id": "cc", "provider": "claude", "kind": "subscription_login", **config}


def test_typed_check_model_cannot_repair_missing_registration_catalog():
    """The old registration form's empty catalog reproduces the screenshot error."""
    for config in ({}, {"models": []}, {"models": [{"id": "opus-5.5"}]}):
        with pytest.raises(ValueError, match="default_model must be an enabled model"):
            catalog.validate_settings(native(**config), "opus-5.5")


@pytest.mark.parametrize("route", ["subscription_login", "api_profile"])
def test_explicit_model_name_is_preserved_for_native_and_gateway(route):
    entry = native(default_model="opus-5.5", models=[{"id": "opus-5.5"}])
    if route == "api_profile":
        entry.update(kind="openai_compatible_chat", provider="gateway",
                     protocols={"anthropic_messages": {}})
    original = deepcopy(entry)
    assert catalog.validate_settings(entry, "opus-5.5") == ("opus-5.5", "")
    assert catalog.model_catalog(entry)["default_model"] == "opus-5.5"
    assert entry == original  # no rewriting upstream aliases or saved configuration


@pytest.mark.parametrize("models,default", [
    ([{"id": "opus-5.5", "enabled": False}], "opus-5.5"),
    ([{"id": "opus-5.5"}], "other"),
    ([{"id": "opus-5.5"}, {"id": "opus-5.5"}], "opus-5.5"),
    ([{"id": "opus-5.5", "support_efforts": ["unknown"]}], "opus-5.5"),
])
def test_invalid_catalog_remains_rejected(models, default):
    with pytest.raises(ValueError):
        catalog.validate_settings(native(models=models, default_model=default), "opus-5.5")


def test_saved_alias_and_disabled_selection_keep_their_contract():
    entry = native(default_model="provider-model", models=[
        {"id": "provider-model", "aliases": ["opus"], "support_efforts": ["high"]},
        {"id": "disabled-model", "enabled": False},
    ])
    assert catalog.validate_settings(entry, "opus", "high") == ("provider-model", "high")
    with pytest.raises(ValueError, match="not enabled"):
        catalog.validate_settings(entry, "disabled-model")
