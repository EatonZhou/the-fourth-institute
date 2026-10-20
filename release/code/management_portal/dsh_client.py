"""DSH process lifecycle at the Provider boundary. No Portal database access."""
from __future__ import annotations
from management_common.portal_paths import path as portal_path
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config

from management_common.file_lock import lock_stream
from management_common.process_runtime import spawn_process, terminate_process_tree
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import yaml

from management_portal.native_agent_transport import NativeAgentClient, NativeAgentError, NativeAgentUnavailable
from management_portal import dsh_provider_catalog
from management_portal.dsh_subagent_cards import DshSubagentCards
from management_common.provider_subagent_runs import STOPPING_METHOD
from management_portal.resident_shell_environment import resident_process_environment
from management_portal.ai_provider_registry import shared_ai_provider_registry

RUNTIME = Path(os.environ.get(
    "PORTAL_DSH_RUNTIME_ROOT",
    str(resolve_directory(read_site_config().get("deployment", {}).get("providers", {}).get(
        "runtime_root", str(portal_path("staging_root") / "provider-runtimes"))) / "dsh-runtime-current"),
))
STATE = Path(os.environ.get("PORTAL_DSH_STATE", str(portal_path("staging_root") / "shared" / "dsh")))
SOCKETS = Path(os.environ.get("PORTAL_DSH_SOCKETS", str(portal_path("staging_root") / "portal_locks" / "dsh")))


def _profile_modules(profile):
    """Install dependencies before selecting them, without Windows symlink rights."""
    modules = profile / "node_modules"
    if os.name != "nt":
        if not modules.exists():
            modules.symlink_to(RUNTIME / "node_modules", target_is_directory=True)
        return
    source = RUNTIME.resolve()
    lock = source / "package-lock.json"
    identity = hashlib.sha256(str(source).encode() + (lock.read_bytes() if lock.exists() else b"")).hexdigest()
    marker = profile / ".portal-runtime"
    if modules.is_dir() and marker.is_file() and marker.read_text() == identity:
        return
    temporary = Path(tempfile.mkdtemp(prefix=".modules-", dir=profile))
    old = temporary / "previous"
    candidate = temporary / "candidate"
    try:
        shutil.copytree(source / "node_modules", candidate)
        if modules.exists():
            modules.rename(old)
        try:
            candidate.rename(modules)
        except BaseException:
            if old.exists():
                old.rename(modules)
            raise
        marker.write_text(identity)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def credential_paths() -> tuple[Path, ...]:
    return (STATE,)


# DSH condenses older history when the context reaches this share of the
# model's window (its own default).  The Harness may ask for an earlier one.
DEFAULT_COMPACTION_RATIO = 0.8
# An on-demand compaction writes one summary with the session's model; Portal
# waits 10 minutes for an explicit compaction.
COMPACT_TIMEOUT_SECONDS = 590.0


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def session_id_for(turn_id: str) -> str:
    return "dsh-" + _digest(turn_id)


def compaction_ratio(value) -> float:
    """A usable compaction threshold ratio, or 0 for "not requested"."""

    try:
        ratio = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(min(0.95, max(0.05, ratio)), 3) if ratio > 0 else 0.0


def _launched(session_id) -> dict:
    """What the session's DSH process was last started with (``launch.json``)."""

    try:
        launched = json.loads((STATE / session_id / "launch.json").read_text())
    except (OSError, ValueError):
        return {}
    return launched if isinstance(launched, dict) else {}


def launched_compaction_ratio(session_id) -> float:
    """The threshold ratio the session's DSH process was last started with."""

    return compaction_ratio(_launched(session_id).get("threshold_ratio"))


def launched_with_subagents(session_id) -> bool | None:
    """Whether the session's DSH process was started with the subagent tool; ``None`` when unknown."""

    value = _launched(session_id).get("subagents")
    return value if isinstance(value, bool) else None


class DshClient:
    def _transport(self, session_id):
        if not session_id or not session_id.startswith("dsh-") or any(c not in "0123456789abcdefdsh-" for c in session_id):
            raise ValueError("Invalid DSH native session identity")
        return NativeAgentClient(SOCKETS / f"{_digest(session_id)}.sock")

    def _start(self, session_id, params):
        transport = self._transport(session_id)
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        SOCKETS.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = STATE / session_id
        root.mkdir(exist_ok=True, mode=0o700)
        with (root / "launch.lock").open("a+") as lock, lock_stream(lock):
            try:
                transport._request("health", response_timeout=1)
                return transport
            except NativeAgentUnavailable:
                pass
            row = dsh_provider_catalog.profile(params["provider_profile"])
            credential = shared_ai_provider_registry.read_secret(shared_ai_provider_registry.secret_path(row))
            env, identity = resident_process_environment(params)
            base_url = shared_ai_provider_registry.base_url(row, protocol="anthropic")
            # Web search is a request of its own to the route's Messages endpoint, with the route's key.
            env.update(DSH_HOME=str(root), DEEPSEEK_API_KEY=credential, DEEPSEEK_BASE_URL=base_url,
                       DEEPSEEK_SEARCH_BASE_URL=base_url + "/v1")
            profile = root / "profiles" / "portal-resident"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "package.json").write_text(json.dumps({"name": "portal-resident", "private": True,
                "dsh": {"profile": {"bundles": [], "patchReload": "startup"}}}))
            _profile_modules(profile)
            # Portal owns the bridge; the installed runtime owns only npm dependencies.
            bridge = profile / "bridge.mjs"
            shutil.copyfile(Path(__file__).parent / "dsh_runtime/bridge.mjs", bridge)
            shutil.copyfile(Path(__file__).parent / "dsh_runtime/request_timing.mjs", profile / "request_timing.mjs")
            ipc_module = Path(__file__).resolve().parents[1] / "management_common/local_ipc.cjs"
            (profile / "local_ipc.cjs").write_text("module.exports = require(" + json.dumps(str(ipc_module)) + ");\n", encoding="utf-8")
            rows = []
            def plugin(name, config=None, **extra):
                rows.append({"id": name, "name": "@deepseek-ai/" + name, **({"config": config} if config else {}), **extra})
            for name in ("cordis-plugin-timer", "dsh-llm", "dsh-session", "dsh-session-projection", "dsh-tools", "dsh-agent"):
                plugin(name)
            plugin("dsh-system-prompt", {"includeHarnessIdentity": False, "includeRuntimeContext": False})
            plugin("dsh-llm-deepseek-api-key", {"apiKeyEnv": "DEEPSEEK_API_KEY", "streamIdleTimeoutMs": 120000})
            plugin("dsh-session-persistence-jsonl", {"root": str(root / "sessions"), "compression": "none"})
            # Without a compaction backend a DSH session only grows until the
            # model refuses it.  The ratio is fixed when the process starts.
            ratio = compaction_ratio(params.get("auto_compact_ratio")) or DEFAULT_COMPACTION_RATIO
            plugin("dsh-token-meter")
            plugin("dsh-compaction-tool-result-pruner")
            plugin("dsh-compaction-basic", {"thresholdRatio": ratio, "retainRatio": round(min(0.16, ratio / 2), 3)})
            # The Resident's "允许开启 sub agent": DSH's delegation tool, in the
            # foreground, so a child runs inside its tool call and the turn.
            subagents = params.get("allow_subagents") is not False
            (root / "launch.json").write_text(json.dumps({"threshold_ratio": ratio, "subagents": subagents}))
            plugin("dsh-session-checkpoint-policy")
            plugin("dsh-sandbox-local")
            plugin("dsh-sandbox-policy", {"mode": "danger-full-access", "workspaceRoot": params["cwd"]})
            plugin("dsh-subprocess-local")
            plugin("dsh-terminal")
            plugin("dsh-terminal-bash", {"timeoutMs": 300000})
            plugin("dsh-tool-bash-persistent", {"timeoutMs": 300000})
            plugin("dsh-fs-local")
            plugin("dsh-tool-fs")
            # The tools the other Providers' models have besides the shell and
            # files: file search, a todo list, web search and fetch.
            plugin("dsh-tool-fs-search", {"sampleOverCapGlobResults": False})
            plugin("dsh-tool-todo", {"allowParallelInProgress": True})
            plugin("dsh-web", {"searchProvider": "deepseek-official", "fetchProvider": "http"})
            plugin("dsh-web-search-deepseek", {"apiKeyEnv": "DEEPSEEK_API_KEY"})
            plugin("dsh-web-fetch-http")
            plugin("dsh-tool-web", {"fetch": True, "searchTimeoutMs": 60000})
            if subagents:
                plugin("dsh-subagent")
                plugin("dsh-subagent-spawn-in-process", {"providerName": "spawn"})
                plugin("dsh-tool-subagent", {"provider": "spawn", "toolName": "subagent", "enableRunInBackground": False})
            plugin("dsh-agent-loop", {"agents": []})
            rows.append({"id": "portal-control", "name": str(bridge), "config": {
                "sessionId": session_id, "socketPath": str(transport.socket_path), "cwd": params["cwd"],
                "pythonExecutable": sys.executable,
                "route": params["provider_profile"],
                "identityFingerprint": hashlib.sha256(json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()}})
            (profile / "cordis.patch.yml").write_text(yaml.safe_dump([{"insert": rows}], sort_keys=False))
            launcher = RUNTIME / "node_modules/@deepseek-ai/dsh/lib/bin.js"
            if not launcher.is_file():
                raise RuntimeError("DSH runtime missing; install a validated shared DSH runtime")
            log_path = root / "runtime.log"
            with log_path.open("ab") as log:
                os.chmod(log_path, 0o600)
                host = Path(__file__).resolve().parents[1] / "management_common/process_host.py"
                process = spawn_process([sys.executable, "-I", str(host), "node", str(launcher), "--profile", "portal-resident"],
                    cwd=params["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, kill_on_owner_exit=False)
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise NativeAgentUnavailable(f"DSH startup failed; inspect {log_path}")
                    try:
                        transport._request("health", response_timeout=1)
                        return transport
                    except NativeAgentUnavailable:
                        time.sleep(.1)
                raise NativeAgentUnavailable(f"DSH startup timed out; inspect {log_path}")
            except BaseException:
                terminate_process_tree(process, timeout=10)
                raise

    def run_turn(self, *, event_callback=None, tool_callback=None, detach_requested=None, **params):
        """Run a turn, or attach to the running turn with this ``turn_id``.

        ``detach_requested`` lets a retiring Worker leave the turn running;
        ``continue_prompt`` continues a turn an earlier attempt started that
        is no longer running (the bridge never sends its input twice).
        """
        params["model"], params["effort"] = self._settings(params)
        if not params.get("is_owner") or params.get("permission_profile") not in {"owner", "danger-full-access"}:
            raise ValueError("DSH profile requires an authorized full-access execution")
        session_id = params.get("session_id") or session_id_for(params["turn_id"])
        params["session_id"] = session_id
        transport = self._start(session_id, params)
        if event_callback:
            event_callback({"method": "thread/started", "params": {"thread": {"id": session_id}}})
            # The subagents' cards follow the events they come from.
            cards, provider_callback = DshSubagentCards(session_id), event_callback

            def event_callback(event):
                if event.get("method") != STOPPING_METHOD:
                    provider_callback(event)
                for card in cards.observe(event):
                    provider_callback(card)
        result = transport._request("turn/run", params, event_callback=event_callback, tool_callback=tool_callback,
                                    detach_requested=detach_requested)
        return SimpleNamespace(**result)


    def compact_session(self, *, session_key="", session_id, **params):
        """Compact an idle session's history now (what DSH's /compact runs).

        An older span of the history becomes one summary written by the
        session's model; the recent history stays.  ``compacted`` is false
        when DSH finds no span worth compacting.
        """

        params["model"], params["effort"] = self._settings(params)
        request = {key: params[key] for key in (
            "cwd", "provider_profile", "model", "effort", "resident_environment") if key in params}
        return self._start(session_id, params)._request(
            "session/compact", {**request, "session_id": session_id}, response_timeout=COMPACT_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _settings(params):
        from management_portal.execution.provider_catalog import AGENT_PROVIDERS
        return AGENT_PROVIDERS["dsh"].validate_settings(
            params["provider_profile"], params.get("model", ""), params.get("effort", ""))

    def steer(self, session_key, session_id, *, turn_id, text, control_id=""):
        """Give the running turn more input; DSH takes it at the turn's next step."""

        if not session_id or not turn_id:
            return {"steered": False, "reason": "no_active_turn"}
        try:
            return self._transport(session_id)._request("turn/steer", {
                "session_id": session_id, "turn_id": turn_id, "text": text, "control_id": control_id,
            }, response_timeout=10)
        except NativeAgentUnavailable:
            return {"steered": False, "reason": "dsh_unavailable"}

    def stop_subagent(self, session_key, session_id="", *, tool_use_id):
        """Stop one subagent the running turn's model started (its ``subagent`` call); the turn goes on."""

        if not session_id or not tool_use_id:
            return {"stopped": False, "reason": "unknown_subagent"}
        try:
            return self._transport(session_id)._request("subagent/stop", {
                "session_id": session_id, "tool_use_id": tool_use_id}, response_timeout=10)
        except NativeAgentUnavailable:
            return {"stopped": False, "reason": "dsh_unavailable"}
        except NativeAgentError as error:
            # A DSH process started before Portal could stop a subagent runs the bridge it started with.
            if "Unsupported native operation" in str(error):
                return {"stopped": False, "reason": "subagent_stop_unsupported"}
            raise

    def interrupt(self, session_key, session_id, *, turn_id=""):
        if not session_id:
            return {"interrupted": False}
        try:
            return self._transport(session_id)._request("turn/interrupt", {"session_id": session_id, "turn_id": turn_id}, response_timeout=5)
        except NativeAgentUnavailable:
            return {"interrupted": False}

    def close_session(self, session_key, session_id):
        if not session_id:
            return {}
        # Acknowledgement precedes native process exit. Serialize release with
        # startup so a following turn cannot attach to the dying socket.
        self._transport(session_id)  # validate before constructing any state path
        if not (STATE / session_id).is_dir():
            return {"closed": True}
        with (STATE / session_id / "launch.lock").open("a+") as lock, lock_stream(lock):
            transport = self._transport(session_id)
            try:
                result = transport._request("session/close", {"session_id": session_id}, response_timeout=10)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                    try:
                        transport._request("health", response_timeout=1)
                    except NativeAgentUnavailable:
                        return result
                raise RuntimeError("DSH native release has not completed")
            except NativeAgentUnavailable:
                return {"closed": True}


def shared_client():
    return DshClient()
