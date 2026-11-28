"""OpenCode process lifecycle at the Provider boundary. No Portal database access.

Each Portal session of an OpenCode Resident has one bridge process
(``opencode_bridge``) on its own Unix socket; the bridge attaches to the native HTTP server
for the account and shell authority, with the session's working directory.  ``OpenCodeClient`` starts the
bridge when the session has none and speaks the native agent protocol to it.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

from management_common.file_lock import lock_stream
from management_common.process_runtime import spawn_process, terminate_process_tree
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from management_common.provider_subagent_runs import PROGRESS_METHOD, STOPPING_METHOD, SubagentRuns
from management_portal.native_agent_transport import NativeAgentClient, NativeAgentError, NativeAgentUnavailable
from management_portal import opencode_provider_catalog
from management_portal.resident_shell_environment import (
    resident_identity_fingerprint, resident_identity_only, resident_process_environment,
)
from management_portal.execution.provider_session_features import session_features_enabled

PROVIDER = "opencode"
STATE = portal_path("provider-state") / "opencode"
SOCKETS = portal_path("runtime") / "opencode"
BRIDGE = Path(__file__).with_name("opencode_bridge.py")
SESSION_ID = re.compile(r"opencode-[0-9a-f]{32}")
# An on-demand compaction writes one summary with the session's model.
COMPACT_TIMEOUT_SECONDS = 1770.0
# OpenCode's delegation tool; its subagent runs in a child session.
SUBAGENT_TOOL = "task"
# How the client gives the bridge the route's key (``opencode_bridge``).
KEY_VARIABLE = "PORTAL_OPENCODE_API_KEY"


class OpenCodeStartupError(NativeAgentUnavailable):
    """A local runtime failure; safe classification survives the Execution boundary."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.error_code = code


def credential_paths() -> tuple[Path, ...]:
    """OpenCode's session stores: isolated test groups hide them."""

    return (STATE,)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def session_for_turn(turn_id: str) -> str:
    """Portal names a new OpenCode session after the turn that starts it."""

    return "opencode-" + digest(turn_id)


class OpenCodeSubagentCards:
    """The cards of the subagents (OpenCode's ``task`` calls) of one turn."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.turn_id = ""
        self.runs = SubagentRuns()
        # The runs the bridge stopped: their calls end failed, their cards stopped.
        self.stopping: set[str] = set()
        # The calls that have their result.  An aborted child session still
        # updates its steps after its call failed; that is no new run.
        self.ended: set[str] = set()

    def _event(self, method: str, item: dict) -> dict:
        return {"method": method, "params": {"threadId": self.session_id, "turnId": self.turn_id, "item": item}}

    def _start(self, call: str, arguments: object = None) -> None:
        if not self.runs.knows(call):
            agent = str(arguments.get("subagent_type") or "general") if isinstance(arguments, dict) else "general"
            self.runs.start(call, tool_use_id=call, agent_type=agent)
        if isinstance(arguments, dict):
            self.runs.describe(call, description=str(arguments.get("description") or ""),
                               prompt=str(arguments.get("prompt") or ""))

    def _progress(self, call: str) -> list[dict]:
        card = self.runs.live(call, turn_id=self.turn_id)
        return [] if card is None else [self._event(PROGRESS_METHOD, card)]

    def observe(self, event: dict) -> list[dict]:
        method = str(event.get("method") or "")
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        if method == STOPPING_METHOD:
            self.stopping.add(str(params.get("toolUseId") or ""))
            return []
        if method == "subagent/event":
            call = str(params.get("parentToolUseId") or "")
            if call in self.ended:
                return []
            inner = params.get("event") if isinstance(params.get("event"), dict) else {}
            if call and inner.get("method") == "usage/updated":
                # The bridge reports the subagent's running total of tokens; the
                # card names the reasoning ones as every Provider's card does.
                usage = (inner.get("params") or {}).get("usage") or {}
                self._start(call)
                self.runs.report_usage(call, {**usage, "reasoning_tokens": usage.get("reasoning_output_tokens", 0)})
                return self._progress(call)
            item = (inner.get("params") or {}).get("item") or {}
            if not call or inner.get("method") not in {"item/started", "item/completed"}:
                return []
            if item.get("type") == "agentMessage":
                step = {"type": "agentMessage", "text": str(item.get("text") or "")}
            elif item.get("type") == "dynamicToolCall":
                status = str(item.get("status") or "running")
                step = {"type": "toolCall", "id": str(item.get("id") or ""), "name": str(item.get("tool") or "tool"),
                        "input": item.get("arguments") or {}, "status": status, "error": item.get("success") is False}
            else:
                return []
            self._start(call)
            # The child session's own trace is keyed by its session, not by the call.
            self.runs.describe(call, trace_ref=str(params.get("traceKey") or ""))
            self.runs.observe(call, step, model=str(params.get("model") or ""))
            return self._progress(call)
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        self.turn_id = str(turn.get("id") or params.get("turnId") or self.turn_id)
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if item.get("type") != "dynamicToolCall" or item.get("tool") != SUBAGENT_TOOL or not item.get("id"):
            return []
        call = str(item["id"])
        self._start(call, item.get("arguments"))
        if method == "item/started":
            return self._progress(call)
        if method != "item/completed":
            return []
        output = item.get("output")
        text = "\n".join(str(part.get("text") or "") for part in output if isinstance(part, dict)) if isinstance(output, list) else str(output or "")
        # OpenCode wraps the subagent's answer: <task id=...><task_result>answer</task_result></task>.
        answer = re.search(r"<task_result>\s*(.*?)\s*</task_result>", text, re.S)
        text = answer.group(1) if answer else text
        failed = item.get("success") is False
        stopped = failed and call in self.stopping
        self.stopping.discard(call)
        self.ended.add(call)
        card = self.runs.finish(call, status="stopped" if stopped else "failed" if failed else "completed",
                                result=text, end_id=call, turn_id=self.turn_id)
        return [] if card is None else [self._event("item/completed", card)]


def account_scope(route, identity, inherit_database):
    return digest(json.dumps([route, identity, bool(inherit_database)], sort_keys=True))


class OpenCodeClient:
    def _transport(self, session_id: str) -> NativeAgentClient:
        if not SESSION_ID.fullmatch(str(session_id or "")):
            raise ValueError("Invalid OpenCode native session identity")
        return NativeAgentClient(SOCKETS / f"{digest(session_id)}.sock")

    def _start(self, session_id: str, params: dict) -> NativeAgentClient:
        transport = self._transport(session_id)
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        SOCKETS.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = STATE / session_id
        root.mkdir(exist_ok=True, mode=0o700)
        with (root / "launch.lock").open("a+") as lock, lock_stream(lock):
            binary = opencode_provider_catalog.binary()
            if not binary.is_file():
                raise OpenCodeStartupError("OpenCode runtime missing; install a validated shared OpenCode runtime",
                                           "provider_runtime_missing")
            # The environment goes to the process only: it may hold the Portal database's address
            # and the route's key, which the bridge hands to OpenCode (``KEY_VARIABLE``).
            environment, identity = resident_process_environment(params)
            route = params["provider_profile"]
            key = opencode_provider_catalog.api_key(route)
            if key:
                environment[KEY_VARIABLE] = key
            service = opencode_provider_catalog.service(route)
            config = {
                "session_id": session_id, "socket_path": str(transport.socket_path), "state_dir": str(root),
                "cwd": params["cwd"], "route": route, "service": service,
                # A custom route's provider definition holds no key (``api_key``).
                "provider": opencode_provider_catalog.provider_config(route),
                "binary": str(binary.parent.resolve() / binary.name),
                "config_home": str(opencode_provider_catalog.config_home()),
                "inherit_portal_database": bool(params.get("inherit_portal_database")),
                "version": opencode_provider_catalog.version(),
                "snapshot": session_features_enabled("opencode"),
                # The whole shell environment the bridge checks each turn against.
                "identity_fingerprint": hashlib.sha256(
                    json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
                # The Resident alone: Workspace bindings may change between turns.
                "resident_fingerprint": hashlib.sha256(json.dumps(
                    resident_identity_only(identity), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).encode()).hexdigest(),
            }
            # Existing session databases stay at their original location. New native
            # sessions share storage only within the account and inherited shell authority.
            scope = account_scope(route, resident_identity_only(identity), config["inherit_portal_database"])
            # A changed environment (new Workspace variables) is a new generation:
            # an idle bridge is relaunched so its shells see the current values.
            generation = digest(json.dumps([scope, config["binary"], config["version"], config["provider"],
                                            digest(key or ""), BRIDGE.stat().st_mtime_ns,
                                            config["identity_fingerprint"]], sort_keys=True))
            config["generation"] = generation
            if not (root / "opencode.db").exists():
                config.update(account_state=str(STATE / "accounts" / scope),
                              account_socket=str(SOCKETS / ("a" + generation + ".sock")))
            launch = root / "launch.json"
            if launch.is_file():
                previous = json.loads(launch.read_text())
                # Launches before resident_fingerprint recorded only the whole environment.
                if any(previous.get(key) != config[key] for key in ("route", "cwd")) or (
                        "resident_fingerprint" in previous and previous["resident_fingerprint"] != config["resident_fingerprint"]):
                    raise ValueError("OpenCode session account, shell identity or directory changed")
                if "inherit_portal_database" in previous and previous["inherit_portal_database"] != config["inherit_portal_database"]:
                    raise ValueError("OpenCode session shell authority changed")
            try:
                health = transport._request("health", response_timeout=3)
                if health.get("generation") == generation:
                    return transport
                # A Worker may be reattaching to a submitted turn in the old generation.
                status = transport._request("session/status", {"session_id": session_id}, response_timeout=10)
                if status.get("active") or (status.get("native_status") or {}).get("type") in {"busy", "retry"}:
                    return transport
                self._close_transport(transport, session_id)
            except NativeAgentUnavailable:
                pass
            temporary = root / f".launch.{os.getpid()}.tmp"
            temporary.write_text(json.dumps(config))
            os.chmod(temporary, 0o600)
            temporary.replace(launch)
            log_path = root / "runtime.log"
            with log_path.open("ab") as log:
                os.chmod(log_path, 0o600)
                try:
                    process = spawn_process([sys.executable, "-I", str(BRIDGE), str(launch)], cwd=params["cwd"], env=environment,
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log, kill_on_owner_exit=False)
                except (OSError, ValueError) as exc:
                    raise OpenCodeStartupError("OpenCode bridge could not be launched", "provider_runtime_launch_failed") from exc
            try:
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise OpenCodeStartupError(f"OpenCode startup failed; inspect {log_path}", "provider_runtime_startup_failed")
                    try:
                        transport._request("health", response_timeout=3)
                        return transport
                    except NativeAgentUnavailable:
                        time.sleep(.2)
                raise OpenCodeStartupError(f"OpenCode startup timed out; inspect {log_path}", "provider_runtime_startup_timeout")
            except BaseException:
                terminate_process_tree(process, force=True)
                raise

    @staticmethod
    def _settings(params: dict) -> tuple[str, str]:
        from management_portal.execution.provider_catalog import AGENT_PROVIDERS

        return AGENT_PROVIDERS[PROVIDER].validate_settings(
            params["provider_profile"], params.get("model", ""), params.get("effort", ""))

    def run_turn(self, *, event_callback=None, tool_callback=None, detach_requested=None, **params):
        """Run a turn, or attach to the running turn with this ``turn_id``."""

        params["model"], params["effort"] = self._settings(params)
        # OpenCode runs its tools without asking: only a full-access execution may use it.
        if not params.get("is_owner") or params.get("permission_profile") not in {"owner", "danger-full-access"}:
            raise ValueError("OpenCode requires an authorized full-access execution")
        session_id = params.get("session_id") or session_for_turn(params["turn_id"])
        params["session_id"] = session_id
        transport = self._start(session_id, params)
        if event_callback:
            event_callback({"method": "thread/started", "params": {"thread": {"id": session_id}}})
            cards, provider_callback = OpenCodeSubagentCards(session_id), event_callback

            def event_callback(event):
                if event.get("method") != STOPPING_METHOD:
                    provider_callback(event)
                for card in cards.observe(event):
                    provider_callback(card)
        result = transport._request("turn/run", params, event_callback=event_callback, tool_callback=tool_callback,
                                    detach_requested=detach_requested)
        return SimpleNamespace(**result)

    def fork_session(self, *, source_session_id, fork_key, cwd, provider_profile, message_id="", **options):
        from management_common.file_permissions import atomic_write_private_bytes
        if not fork_key:
            raise ValueError("Missing fork execution identity")
        target = session_for_turn("fork:" + fork_key)
        root = STATE / target
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = root / "state.json"
        with (root / "launch.lock").open("a+") as lock, lock_stream(lock):
            if state.exists():
                return {"session_id": target}
            export = self._transport(source_session_id)._request("session/fork", {
                "session_id": source_session_id, "provider_profile": provider_profile,
                "fork_key": fork_key, "message_id": message_id}, response_timeout=180)
            # Import the native export into the child's own database, also when the
            # destination has a different Resident identity or working directory.
            import tempfile
            native = export["info"]["id"]
            identity = resident_identity_fingerprint(options.get("resident_environment"))
            scope = account_scope(provider_profile, resident_identity_only(identity), options.get("inherit_portal_database"))
            account = STATE / "accounts" / scope
            account.mkdir(mode=0o700, parents=True, exist_ok=True)
            env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TMPDIR") if k in os.environ}
            env.update(HOME=str(root), XDG_DATA_HOME=str(root / "data"), XDG_CONFIG_HOME=str(root / "config"),
                       XDG_STATE_HOME=str(root / "state"), OPENCODE_DB=str(account / "opencode.db"),
                       OPENCODE_DISABLE_PROJECT_CONFIG="1", OPENCODE_DISABLE_AUTOUPDATE="1",
                       OPENCODE_DISABLE_MODELS_FETCH="1")
            with tempfile.TemporaryDirectory(dir=root) as tmp:
                path = Path(tmp) / "session.json"
                atomic_write_private_bytes(path, json.dumps(export).encode())
                binary = str(opencode_provider_catalog.binary())
                subprocess.run([binary, "import", str(path)], cwd=tmp, env=env, check=True,
                               capture_output=True, timeout=120)
                check = subprocess.run([binary, "export", native], cwd=tmp, env=env, check=True,
                                       capture_output=True, timeout=120)
                if json.loads(check.stdout)["info"]["id"] != native:
                    raise NativeAgentError("OpenCode failed to import the fork")
            atomic_write_private_bytes(state, json.dumps({"opencode_session_id": native, "turns": {}}).encode())
        return {"session_id": target}

    def session_command(self, command, params, argument):
        params = {**params, "resident_environment": resident_identity_fingerprint(params.get("resident_environment"))}
        transport = self._transport(params["session_id"])
        try:
            transport._request("health", response_timeout=3)
        except NativeAgentUnavailable:
            transport = self._start(params["session_id"], params)
        return transport._request("session/" + command, {**params, "argument": argument}, response_timeout=180)

    def requests(self, session_id: str, *, since_ms: int) -> dict:
        """Every model request of the session since ``since_ms``, from OpenCode's own record."""

        try:
            return self._transport(session_id)._request("usage/read", {"since_ms": since_ms}, response_timeout=180)
        except NativeAgentUnavailable:
            return {"requests": []}

    def latest_compaction(self, session_id: str) -> dict:
        """The newest compaction OpenCode committed in the session, ``{}`` when it has none."""

        try:
            return self._transport(session_id)._request("compaction/read", {}, response_timeout=180)
        except NativeAgentUnavailable:
            return {}

    def compact_session(self, *, session_key="", session_id, **params):
        """Have OpenCode summarize the session's history now (its /compact)."""

        params["model"], params["effort"] = self._settings(params)
        request = {key: params[key] for key in ("cwd", "provider_profile", "model", "resident_environment", "inherit_portal_database") if key in params}
        return self._start(session_id, params)._request(
            "session/compact", {**request, "session_id": session_id}, response_timeout=COMPACT_TIMEOUT_SECONDS)

    def steer(self, session_key, session_id, *, turn_id, text, control_id=""):
        if not session_id or not turn_id:
            return {"steered": False, "reason": "no_active_turn"}
        try:
            return self._transport(session_id)._request("turn/steer", {
                "session_id": session_id, "turn_id": turn_id, "text": text, "control_id": control_id,
            }, response_timeout=30)
        except NativeAgentUnavailable:
            return {"steered": False, "reason": "opencode_unavailable"}

    def stop_subagent(self, session_key, session_id="", *, tool_use_id):
        """Stop one subagent the running turn's model started (its ``task`` call); the turn goes on."""

        if not session_id or not tool_use_id:
            return {"stopped": False, "reason": "unknown_subagent"}
        try:
            return self._transport(session_id)._request("subagent/stop", {
                "session_id": session_id, "tool_use_id": tool_use_id}, response_timeout=30)
        except NativeAgentUnavailable:
            return {"stopped": False, "reason": "opencode_unavailable"}
        except NativeAgentError as error:
            # A bridge started before Portal could stop a subagent runs the code it started with.
            if "Unsupported native operation" in str(error):
                return {"stopped": False, "reason": "subagent_stop_unsupported"}
            raise

    def interrupt(self, session_key, session_id, *, turn_id=""):
        if not session_id:
            return {"interrupted": False}
        try:
            return self._transport(session_id)._request(
                "turn/interrupt", {"session_id": session_id, "turn_id": turn_id}, response_timeout=30)
        except NativeAgentUnavailable:
            return {"interrupted": False}

    def close_session(self, session_key, session_id):
        if not session_id:
            return {}
        self._transport(session_id)  # validate before constructing any state path
        if not (STATE / session_id).is_dir():
            return {"closed": True}
        with (STATE / session_id / "launch.lock").open("a+") as lock, lock_stream(lock):
            return self._close_transport(self._transport(session_id), session_id)

    @staticmethod
    def _close_transport(transport, session_id):
        try:
            result = transport._request("session/close", {"session_id": session_id}, response_timeout=30)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                time.sleep(0.05)
                try:
                    transport._request("health", response_timeout=3)
                except NativeAgentUnavailable:
                    return result
            raise RuntimeError("OpenCode native release has not completed")
        except NativeAgentUnavailable:
            return {"closed": True}


def shared_client() -> OpenCodeClient:
    return OpenCodeClient()
