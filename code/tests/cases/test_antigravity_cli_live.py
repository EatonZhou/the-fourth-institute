"""Opt-in CLI test. Supply a logged-in CLI profile via AGY_TEST_PROFILE."""
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from unittest.mock import Mock

import pytest

from management_portal import antigravity_accounts as accounts, antigravity_runtime as runtime
from management_portal.execution.harness import HarnessTool
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from tests.cases.test_antigravity import context, invocation


@pytest.fixture
def cli_directory():
    # Pytest retains tmp_path between runs. Remove the disposable login and conversations on exit.
    with tempfile.TemporaryDirectory(prefix="portal-agy-blackbox-") as directory:
        yield Path(directory)


@pytest.mark.skipif(not os.getenv("AGY_TEST_PROFILE"), reason="requires a designated Antigravity test account")
def test_real_cli_conversations(monkeypatch, cli_directory):
    home = cli_directory / "profile"
    app = home / "antigravity-acp"
    app.mkdir(parents=True)
    profile = Path(os.environ["AGY_TEST_PROFILE"]) / "antigravity-acp"
    for filename in ("antigravity-oauth-token", "acp_token.json"):
        if (profile / filename).is_file():
            shutil.copy(profile / filename, app / filename)
    work = cli_directory / "workspace"
    work.mkdir()
    monkeypatch.setattr(runtime.updates, "RUNTIMES", cli_directory / "runtimes")
    if archive := os.getenv("AGY_TEST_ARCHIVE"):
        monkeypatch.setattr(runtime, "urlopen", lambda url, timeout: open(archive, "rb"))
    installed = runtime.AntigravityRuntime().stage((runtime.VERSION,))
    monkeypatch.setattr(runtime.AntigravityRuntime, "link", lambda self: installed)
    monkeypatch.setattr(accounts, "environment", lambda _: {
        "HOME": str(cli_directory), "GEMINI_HOME": str(home), "PATH": os.defpath,
        **runtime.credential_environment(),
    })
    monkeypatch.setattr(accounts, "account_runtime", lambda *args: cli_directory / "metadata")
    monkeypatch.setattr(accounts, "account_for_route", lambda _: {"account_id": "test", "route_id": "one"})
    backend = AGENT_PROVIDERS["antigravity"].build_backend()
    label = secrets.token_hex(10)
    callback = Mock(return_value=label)
    tool = HarnessTool("project_label", "Return the project label", {"type": "object", "properties": {}}, callback)

    def run(message, *, operation="turn", session_id="", owner=True, metadata=None):
        call = invocation(work, message, tools=[tool], sid=session_id, owner=owner, execution=secrets.token_hex(8))
        call.request.operation = operation
        call.request.runtime_options.pop("model")
        call.request.metadata = metadata or {}
        observer = context()[0]
        observer._compaction_observer = Mock()
        return backend.execute(call, observer)

    first = run("Call project_label and return the exact value.")
    assert label in first.output["message"]
    callback.assert_called_once()
    sid = first.backend_session_id
    compacted = run("", operation="compact", session_id=sid)
    assert compacted.output["compacted"] and compacted.backend_session_id == sid
    recall = "Recall the project label from this conversation. Reply with its exact value without calling tools."
    assert label in run(recall, session_id=sid).output["message"]
    restored = run("Reply with OK.", owner=False)
    assert restored.backend_session_id != sid
    secret = secrets.token_hex(12)
    file = work / "marker.txt"
    file.write_text(secret)
    check = run(f"Call project_label. Also try reading {file} using the native file reader. Return the values.",
                session_id=restored.backend_session_id, owner=False)
    assert check.backend_session_id == restored.backend_session_id
    assert label in check.output["message"] and secret not in check.output["message"]
    assert callback.call_count == 2
