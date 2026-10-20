"""A new Provider is one declaration and its own Adapter.

Portal reaches every Provider through its declaration in the Provider catalog
(``AgentProvider``) and its capabilities, never through its name.  This test
declares a Provider Portal does not use (``hypothetical_provider``: a
Qwen Code-like agent with its own route catalog and an Adapter on the native
agent socket) in a fresh process, before anything else is imported, as its
line in ``provider_catalog`` would, and checks that every generic part takes
it up: execution requests, accounts and catalogs, Resident registration and
settings, the Harness, slash commands, prompt placement, session tools, the
network check, runtime updates, the ISA, test isolation and a turn.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_framework_isolation_hides_provider_absolute_paths(monkeypatch, tmp_path):
    """框架隔离把 kimi 与 dsh 的凭证目录按真实路径列入屏蔽清单"""
    from management_portal import dsh_client
    from management_portal.test_framework.isolation import credential_paths

    kimi = tmp_path / "kimi-store"
    dsh = tmp_path / "dsh-store"
    kimi.mkdir()
    dsh.mkdir()
    alias = tmp_path / "kimi-alias"
    alias.symlink_to(kimi, target_is_directory=True)
    monkeypatch.setenv("PORTAL_KIMI_CODE_HOME", str(alias))
    monkeypatch.setattr(dsh_client, "STATE", dsh)
    paths = credential_paths(tmp_path / "home")
    assert kimi in paths
    assert dsh in paths

def test_framework_isolation_hides_site_account_and_secret_roots(monkeypatch, tmp_path):
    """站点目录不在 HOME 下或通过别名配置时，也遮蔽全部认证与密钥。"""
    from management_portal.test_framework.isolation import credential_paths

    accounts = tmp_path / "account-store"
    secrets = tmp_path / "secret-store"
    accounts.mkdir()
    secrets.mkdir()
    alias = tmp_path / "account-alias"
    alias.symlink_to(accounts, target_is_directory=True)
    monkeypatch.setenv("PORTAL_ACCOUNTS_ROOT", str(alias))
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(secrets))
    from management_common import portal_paths
    values = portal_paths.path_values()
    monkeypatch.setattr(portal_paths, "path_values", lambda: {**values, "accounts_root": str(alias), "secrets_dir": str(secrets)})
    paths = credential_paths(tmp_path / "empty-home")
    assert accounts in paths
    assert secrets in paths

SCRIPT = r'''
import json, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.cases import hypothetical_provider as hp
hp.register()

from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution import harness_capabilities as hc
definition = AGENT_PROVIDERS[hp.PROVIDER]
out = {"native_commands": list(definition.native_commands()),
       "allow_subagents": definition.runtime_adapter().defaults(definition, {})["allow_subagents"]}

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
def request(operation):
    try:
        return ExecutionRequest.new(actor="sample_admin", prompt="hi", service_type="agent_session", provider=hp.PROVIDER,
                                    operation=operation, agent_id="resident_x",
                                    session=SessionOptions(mode="persistent", binding_id="resident:resident_x")).operation
    except ValueError as error:
        return str(error)
out["requests"] = [request("turn"), request("fork")]

from management_portal.execution.accounts import default_execution_accounts
accounts = default_execution_accounts()
row = next(row for row in accounts.runtime_catalog(include_backends=True)["backends"] if row["backend"] == hp.PROVIDER)
account = accounts.resolve(hp.PROVIDER, "")
out["catalog"] = {"label": row["label"], "settings": row["runtime_settings"], "routes": [route["models"] for route in row["routes"]],
                  "account_models": accounts.runtime_catalog(account.account_id)["models"]}

from management_portal.society_service import resident_execution_provider as residents
projection = residents.capability_projection({"backend": hp.PROVIDER})
out["resident"] = {"registered": residents.validate_registration(hp.PROVIDER, "new_session").backend,
                   "fork": projection["portal_fork"], "turn_input": projection["active_turn_input"],
                   "images": projection["image_input"], "settings": projection["runtime_settings"]}

from management_portal.execution.harness_policy_table import DEFAULT_POLICY, policy_parameters
features = hc.available_feature_ids(hc.provider_capabilities(hp.PROVIDER))
out["harness"] = {"capabilities": sorted(hc.provider_capabilities(hp.PROVIDER)),
                  "tool_governor": "native_tool_loop_governor" in features,
                  "early_compaction": "early_context_compaction" in features,
                  "default_policy": policy_parameters(hp.PROVIDER) == DEFAULT_POLICY}

from management_portal import prompt_injection
from management_portal.execution.session_tools import session_lacks_required_tools
from management_portal.execution import provider_egress, provider_updates
from management_portal.hatg.catalog import resident as isa_resident
from management_portal.test_framework.isolation import credential_paths
home = Path(tempfile.mkdtemp())
(home / ".qwen").mkdir()
out["generic"] = {
    "system_place": prompt_injection.placements(hp.PROVIDER)["core_rules"],
    "session_tools_missing": session_lacks_required_tools({"provider": hp.PROVIDER, "backend_session_id": "s"}),
    "upstream": provider_egress.route_upstream(hp.PROVIDER),
    "network": provider_egress.provider_network_state(hp.PROVIDER),
    "runtime_updated": hp.PROVIDER in provider_updates.runtimes(),
    "isa_provider": hp.PROVIDER in isa_resident.PROVIDERS,
    "credentials_hidden": [str(path.relative_to(home)) for path in credential_paths(home) if path.is_relative_to(home)],
}

# A turn: the Adapter is the native socket Adapter under the Provider's name.
from management_portal.execution.harness import HarnessTool
backend = definition.build_backend()
seen = {}
def run_turn(**kwargs):
    seen.update({key: kwargs[key] for key in ("provider_profile", "allow_subagents")}, tools=[tool["name"] for tool in kwargs["tools"]])
    seen["tool_result"] = kwargs["tool_callback"]({"tool": "read_resource", "arguments": {"id": "r"}, "call_id": "c1"})
    return SimpleNamespace(response="done", session_id="qwencode-session-1", turn_id=kwargs["turn_id"], usage={})
backend.client_factory = lambda: SimpleNamespace(run_turn=run_turn, steer=None, close_session=lambda *a: {})
invocation = SimpleNamespace(
    request=SimpleNamespace(runtime_options={"provider_profile": hp.ROUTE, "tools": [], "session_key": "resident:resident_x"},
                            operation="turn", input_media=(), execution_id="execution-1", cwd="/work", permission_profile="",
                            session=SimpleNamespace(binding_id="resident:resident_x")),
    session=SimpleNamespace(backend_route_id=hp.ROUTE, backend_session_id="qwencode-session-1"),
    user_prompt="audit the module", system_context="", attach_system_context=False, is_new_backend_session=False,
    recovery_checkpoint={}, provider_error_recovery=None, provider_event_processor=None,
    harness_tools=(HarnessTool("read_resource", "Read", {"type": "object"}, lambda arguments: "resource text"),),
    tool_batch_processor=None,
)
context = mock.Mock()
context.is_cancelled.return_value = False
context.execution_started_at = ""
context.has_turn_control_provider.return_value = False
result = backend.execute(invocation, context)
out["turn"] = {**seen, "message": result.output["message"], "provider": result.output["provider"]}
print(json.dumps(out, ensure_ascii=False))
'''


def test_a_provider_is_taken_up_from_its_declaration_alone() -> None:
    """新 Provider 只凭声明即被 Portal 各通用部分接纳

    在新进程中声明一个 Portal 不使用的 Provider，检查执行请求、账号与运行目录、Resident 注册、Harness 规划、斜杠命令、提示词位置、会话工具、网络检查、运行时更新、ISA、脚本沙箱和一轮对话都按声明工作。

    :module: spec.design-governance
    """
    from management_portal.execution.account_registrations import database
    # This declaration test has no built-in API fixture. Do not inherit site
    # defaults whose API metadata is intentionally absent in the child process.
    with database().transaction("provider-extension-fixture") as connection:
        connection.execute("DELETE FROM backend_roles")
        connection.execute("DELETE FROM registrations")
        # Provision this hypothetical Provider's account explicitly. A route
        # catalog describes capabilities; it no longer discovers identities.
        connection.execute("""INSERT INTO registrations
            (account_id,provider,kind,route_id,config,max_concurrency,enabled,
             version,created_at,created_by,updated_at,updated_by)
            VALUES ('qwencode_main','qwencode','service','qwencode_main','{}',8,true,
                    1,'2026-01-01T00:00:00Z','test','2026-01-01T00:00:00Z','test')""")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-3000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])

    # Its operations, settings and switches follow from what it declares.
    assert out["native_commands"] == ["status", "model", "compact"]
    assert out["allow_subagents"] is True
    assert out["requests"] == ["turn", "qwencode does not support fork"]
    models = ["qwen3-coder-plus", "kimi-k2-groq"]
    assert out["catalog"] == {"label": "Qwen Code", "settings": ["model", "reasoning_effort"], "routes": [models],
                              "account_models": models}
    # A Provider that resumes without a session can take a Fork from Portal's context.
    assert out["resident"] == {"registered": "qwencode", "fork": True, "turn_input": True, "images": False,
                               "settings": ["model", "reasoning_effort"]}
    # The Harness plans by capability: the tool governor needs native tool events, early compaction a threshold.
    assert out["harness"] == {
        "capabilities": ["active_turn_input", "error_continuation", "native_tool_events", "portal_tools", "subagents"],
        "tool_governor": True, "early_compaction": False, "default_policy": True,
    }
    assert out["generic"] == {
        "system_place": "Qwen Code 的 agent 指令", "session_tools_missing": False, "upstream": "dashscope",
        # Portal has no probe for its service, so recovery never waits on it.
        "network": "allowed", "runtime_updated": False, "isa_provider": True, "credentials_hidden": [".qwen"],
    }
    assert out["turn"] == {
        "provider_profile": "qwencode_main", "allow_subagents": True, "tools": ["read_resource"],
        "tool_result": {"isError": False, "content": [{"type": "text", "text": "resource text"}]},
        "message": "done", "provider": "qwencode",
    }
