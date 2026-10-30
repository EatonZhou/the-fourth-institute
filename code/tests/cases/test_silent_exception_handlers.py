"""A broad exception handler makes the failure it catches visible.

``except Exception``, ``except BaseException`` and a bare ``except:`` catch
every fault, including the ones nobody meant to tolerate: a typo, a renamed
column, a changed return shape.  A handler that then neither re-raises nor
leaves a trace turns such a fault into a silently wrong answer.  The check
reads every Python file of Portal, the shared packages, the Boards and the
scripts, finds the broad handlers and requires each to do one of these in its
own body (nested functions do not count):

- raise: re-raise, or raise another error;
- log at warning or above: ``exception``, ``error``, ``warning``, ``warn``,
  ``critical`` or ``log`` on a module logger (``LOGGER``, ``logger``, ``LOG``,
  ``_log`` ...), on ``logging`` or ``logging.getLogger(...)``; or
  ``log_error`` (http.server), ``warnings.warn``, ``traceback.print_exc`` /
  ``format_exc``, ``print`` or ``sys.stderr.write``, which is how command-line
  scripts and several services report.  ``info`` and ``debug`` do not count:
  Portal's services configure no handler, so only warnings reach the journal;
- record the exception: read the bound name (``except ... as exc``), so its
  text or type goes into a returned or stored state, a receipt, a response, a
  list of errors, a retry or a later raise;
- record a failure without the exception: write the failure vocabulary
  (``error``, ``errors``, ``failure``, ``failed``, ``fail``, ``fault``,
  ``blocked_reason``, ``warning(s)``, ``unavailable``, ``rejected``,
  ``refused`` as a word of an identifier or a string) as a stored name or
  attribute, a dict or subscript key, a keyword argument, a string value, the
  receiver of ``append``/``add``/``extend``/``update``, or call a function so
  named (``self._fail(...)``, ``_json_error(...)``, ``self.unavailable(...)``)
  or one that notifies or alerts.

A handler that is meant to tolerate a known failure should name it
(``except (TypeError, ValueError)``) instead.  ``ALLOWED`` lists the broad
handlers that stay silent on purpose, by ``path::qualified function`` with
their count and the reason; ``test_allowed_handlers_are_still_silent`` keeps
the table exact.

:module: spec.design-governance
:importance: P1
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("management_portal", "management_common", "scripts", "knowledge_board_lan", "skill_board_lan")
SKIPPED_PARTS = {"node_modules", "__pycache__", "vendor"}
BROAD = {"Exception", "BaseException"}
LOGGER = re.compile(r"^_?(log|logger|LOG|LOGGER|logging)$")
LOG_METHODS = {"exception", "error", "warning", "warn", "critical", "log"}
FAILURE = re.compile(
    r"(^|[_\W])(errors?|failures?|failed|fails?|faults?|blocked_reason|warnings?|unavailable|rejected|refused)"
    r"($|[_\W])", re.IGNORECASE)
FAILURE_CALL = re.compile(r"(error|fail|unavailable|reject|notif|alert)", re.IGNORECASE)
COLLECT = {"append", "add", "extend", "update", "setdefault", "insert", "put"}

# path::qualified function -> (silent broad handlers in it, why they stay silent)
ALLOWED: dict[str, tuple[int, str]] = {
    "management_portal/claude_code_daemon.py::ClaudeCodeDaemon.run_turn.discard_later": (
        1, "waits only for the turn to end; the turn task reports its own outcome"),
    "management_portal/claude_code_daemon.py::WorkerRequest.serve": (
        1, "wait_closed of a writer already closed; the request's own failure was sent or logged above"),
    "management_portal/execution/worker_manager.py::ExecutionWorkerManager._reap": (
        2, "_kill_worker stores cleanup_error before raising; the Worker stays in the list and is retried"),
    "management_portal/execution/worker_manager.py::ExecutionWorkerManager._run": (
        1, "_kill_worker stores cleanup_error before raising; shutdown reports it as unsuccessful below"),
    "management_portal/kimi_code_client.py::KimiCodeClient._request": (
        1, "the HTTP error is raised; only its body was unreadable and str(exc) replaces it"),
    "management_portal/notifications/bus.py::NatsNotificationPublisher._run_async": (
        3, "closing a client after the publish failure was stored in _last_error, or at stop"),
    "management_portal/notifications/bus.py::NatsNotificationListener._run_async": (
        1, "closing a client at stop or after the failure was stored in _last_error"),
    "management_portal/personal_ama.py::_provision_session": (
        1, "a failed probe only provisions again; provisioning reads the same store and reports its failure"),
    "management_portal/resources/isa.py::reads_only": (
        1, "classification only: an unreadable target or undeclared action is recorded as a change, and "
           "RunResourceAction then rejects it with the real error"),
    "scripts/tfi_application_clients.py::plan_disconnected": (
        1, "the failed re-read is recorded as fresh_observation='read_error' in the raised proof"),
    "scripts/tfi_release_windows_service.py::run_command_service.service_main": (
        1, "forced stop of the child after the error was written to the .error.log"),
}


def _python_files() -> list[Path]:
    return sorted(
        path for source in SOURCES for path in (ROOT / source).rglob("*.py")
        if not SKIPPED_PARTS & set(path.relative_to(ROOT).parts)
    )


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any((item.attr if isinstance(item, ast.Attribute) else getattr(item, "id", "")) in BROAD for item in names)


def _body(handler: ast.ExceptHandler):
    """The handler's own nodes; nested functions, lambdas and classes run elsewhere.

    A ``debug`` or ``info`` log call is skipped whole, so its message text does
    not count as a recorded failure.
    """
    pending = list(handler.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"debug", "info"}:
            continue
        yield node
        pending.extend(child for child in ast.iter_child_nodes(node)
                       if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)))


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return str(node.slice.value)
    if isinstance(node, ast.Call) and _name(node.func) == "getLogger":
        return "logger"
    return ""


def _logs(call: ast.Call) -> bool:
    function = _name(call.func)
    receiver = _name(call.func.value) if isinstance(call.func, ast.Attribute) else ""
    return (
        (function in LOG_METHODS and (bool(LOGGER.match(receiver)) or receiver.endswith(("logger", "_log"))))
        or function in {"log_error", "print", "print_exc", "print_exception", "format_exc"}
        or (receiver, function) in {("warnings", "warn"), ("stderr", "write")}
    )


def _records_failure(node: ast.AST) -> bool:
    if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Store):
        return bool(FAILURE.search(_name(node)))
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
        return bool(FAILURE.search(_name(node)))
    if isinstance(node, ast.Dict):
        return any(isinstance(key, ast.Constant) and FAILURE.search(str(key.value)) for key in node.keys)
    if isinstance(node, ast.keyword):
        return bool(node.arg and FAILURE.search(node.arg))
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(FAILURE.search(node.value))
    if isinstance(node, ast.Call):
        if FAILURE_CALL.search(_name(node.func)):
            return True
        return (isinstance(node.func, ast.Attribute) and node.func.attr in COLLECT
                and bool(FAILURE.search(_name(node.func.value))))
    return False


def visible(handler: ast.ExceptHandler) -> bool:
    """Whether the handler re-raises, logs, or records the failure (see the module docstring)."""
    for node in _body(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and _logs(node):
            return True
        if handler.name and isinstance(node, ast.Name) and node.id == handler.name and isinstance(node.ctx, ast.Load):
            return True
        if _records_failure(node):
            return True
    return False


def broad_handlers(path: Path) -> list[tuple[str, int, bool]]:
    """(qualified function, line, visible) for each broad handler in the file."""
    found: list[tuple[str, int, bool]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope + (child.name,) if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else scope
            if isinstance(child, ast.ExceptHandler) and _is_broad(child):
                found.append((".".join(scope) or "<module>", child.lineno, visible(child)))
            visit(child, inner)

    visit(ast.parse(path.read_text(encoding="utf-8")), ())
    return found


def silent_handlers() -> tuple[Counter, dict[str, list[int]]]:
    """``path::function`` -> count of silent broad handlers, and their lines."""
    counts: Counter = Counter()
    lines: dict[str, list[int]] = {}
    for path in _python_files():
        relative = path.relative_to(ROOT).as_posix()
        for function, line, shown in broad_handlers(path):
            if not shown:
                key = f"{relative}::{function}"
                counts[key] += 1
                lines.setdefault(key, []).append(line)
    return counts, lines


def test_broad_handlers_make_failures_visible():
    """宽泛的异常处理要么重新抛出，要么记录日志或失败状态

    读取 Portal、共享包、Board 和脚本中的每个 ``except Exception``、``except BaseException`` 和裸 ``except:``，要求处理块重新抛出、以 warning 及以上级别记录日志、把异常写入返回或保存的状态，或写下失败标记。只想容忍已知失败的处理应写出异常类型；有意保持静默的处理列在 ALLOWED 中并写明原因。
    """
    counts, lines = silent_handlers()
    unexplained = [
        f"{key} (lines {', '.join(map(str, lines[key]))}): {count} silent, {ALLOWED.get(key, (0, ''))[0]} allowed"
        for key, count in sorted(counts.items()) if count > ALLOWED.get(key, (0, ""))[0]
    ]
    assert unexplained == [], (
        "Narrow these handlers to the exceptions they tolerate, or log/record the failure, "
        "or list them in ALLOWED with a reason: " + "; ".join(unexplained))


def test_allowed_handlers_are_still_silent():
    """ALLOWED 中的每个静默异常处理仍然存在且数量一致"""
    counts, _ = silent_handlers()
    stale = [f"{key}: {count} allowed, {counts.get(key, 0)} found"
             for key, (count, _reason) in sorted(ALLOWED.items()) if counts.get(key, 0) != count]
    assert stale == [], "Update or remove these ALLOWED entries: " + "; ".join(stale)
    assert all(reason.strip() for _count, reason in ALLOWED.values())
