from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from management_common import process_runtime
from . import article_identity_backend


_TEXLAB_MANAGERS_LOCK = threading.RLock()
_TEXLAB_MANAGERS: dict[str, "TexLabSession"] = {}


def _texlab_command() -> str:
    for candidate in (
        "texlab",
        str(Path.home() / ".local" / "bin" / "texlab"),
    ):
        executable = shutil.which(candidate)
        if executable:
            return executable
    raise RuntimeError(
        "TexLab is not installed; install TexLab for this platform and add its executable to PATH"
    )


class TexLabSession:
    def __init__(self, article_id: str, root: Path) -> None:
        self.article_id = article_id
        self.root = root.resolve(strict=False)
        self.process: subprocess.Popen[bytes] | None = None
        self._start_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._next_id = 0
        self._pending: dict[int, dict[str, Any]] = {}
        self._opened_versions: dict[str, int] = {}
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._log: list[str] = []
        self._initialized = False
        self._started_at = 0.0

    @property
    def alive(self) -> bool:
        process = self.process
        return process is not None and process.poll() is None

    def ensure_started(self) -> None:
        if self.alive and self._initialized:
            return
        with self._start_lock:
            if self.alive and self._initialized:
                return
            self._terminate()
            executable = _texlab_command()
            self.process = process_runtime.spawn_process(
                [executable],
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._started_at = time.monotonic()
            capabilities = {
                "general": {"positionEncodings": ["utf-16"]},
                "workspace": {
                    "configuration": True,
                    "workspaceFolders": True,
                    "symbol": {"dynamicRegistration": False},
                },
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": False,
                        "didSave": True,
                        "willSave": False,
                    },
                    "completion": {
                        "dynamicRegistration": False,
                        "completionItem": {
                            "snippetSupport": True,
                            "documentationFormat": ["markdown", "plaintext"],
                        },
                    },
                    "hover": {
                        "dynamicRegistration": False,
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "definition": {"dynamicRegistration": False},
                    "documentSymbol": {
                        "dynamicRegistration": False,
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "publishDiagnostics": {
                        "relatedInformation": True,
                        "versionSupport": True,
                    },
                },
            }
            try:
                threading.Thread(
                    target=self._read_stdout,
                    args=(self.process,),
                    name=f"texlab-stdout-{self.article_id}",
                    daemon=True,
                ).start()
                threading.Thread(
                    target=self._read_stderr,
                    args=(self.process,),
                    name=f"texlab-stderr-{self.article_id}",
                    daemon=True,
                ).start()
                self.request(
                    "initialize",
                    {
                        "processId": os.getpid(),
                        "clientInfo": {
                            "name": "TFI Portal Article Workspace",
                            "version": "1",
                        },
                        "locale": "zh-CN",
                        "rootPath": str(self.root),
                        "rootUri": self.root.as_uri(),
                        "workspaceFolders": [
                            {"uri": self.root.as_uri(), "name": self.root.name}
                        ],
                        "capabilities": capabilities,
                        "initializationOptions": {},
                        "trace": "off",
                    },
                    timeout=12,
                    start=False,
                )
                self.notify("initialized", {}, start=False)
                self._initialized = True
            except BaseException:
                self._terminate()
                raise

    def _write(self, message: dict[str, Any]) -> None:
        process = self.process
        if not process or not process.stdin or process.poll() is not None:
            raise RuntimeError("TexLab process is not running")
        encoded = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        frame = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded
        with self._write_lock:
            process.stdin.write(frame)
            process.stdin.flush()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 8,
        start: bool = True,
    ) -> Any:
        if start:
            self.ensure_started()
        with self._state_lock:
            self._next_id += 1
            request_id = self._next_id
            pending = {"event": threading.Event(), "result": None, "error": None}
            self._pending[request_id] = pending
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            if not pending["event"].wait(max(0.1, float(timeout))):
                raise TimeoutError(f"TexLab request timed out: {method}")
            if pending["error"]:
                error = pending["error"]
                if isinstance(error, dict):
                    raise RuntimeError(
                        str(error.get("message") or f"TexLab request failed: {method}")
                    )
                raise RuntimeError(str(error))
            return pending["result"]
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        start: bool = True,
    ) -> None:
        if start:
            self.ensure_started()
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _read_stdout(self, process: subprocess.Popen[bytes]) -> None:
        stream = process.stdout if process else None
        if stream is None:
            return
        try:
            while True:
                content_length = 0
                while True:
                    line = stream.readline()
                    if not line:
                        raise EOFError
                    if line in {b"\n", b"\r\n"}:
                        break
                    key, separator, value = line.decode(
                        "ascii", errors="replace"
                    ).partition(":")
                    if separator and key.casefold().strip() == "content-length":
                        content_length = int(value.strip())
                if content_length <= 0:
                    continue
                payload = stream.read(content_length)
                if len(payload) != content_length:
                    raise EOFError
                message = json.loads(payload.decode("utf-8", errors="replace"))
                with self._state_lock:
                    if self.process is not process:
                        return
                    self._handle_message(message)
        except (EOFError, OSError, ValueError, json.JSONDecodeError) as exc:
            with self._state_lock:
                if self.process is process:
                    self._fail_pending(f"TexLab connection closed: {exc}")

    def _read_stderr(self, process: subprocess.Popen[bytes]) -> None:
        stream = process.stderr if process else None
        if stream is None:
            return
        try:
            for raw_line in iter(stream.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    with self._state_lock:
                        if self.process is not process:
                            return
                        self._log = (self._log + [line])[-80:]
        except (OSError, ValueError):
            return

    def _handle_message(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is not None and not method:
            with self._state_lock:
                pending = self._pending.get(int(request_id))
                if pending:
                    pending["result"] = message.get("result")
                    pending["error"] = message.get("error")
                    pending["event"].set()
            return
        if method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = str(params.get("uri") or "")
            with self._state_lock:
                self._diagnostics[uri] = list(params.get("diagnostics") or [])
            return
        if method in {"window/logMessage", "window/showMessage"}:
            params = message.get("params") or {}
            text = str(params.get("message") or "").strip()
            if text:
                with self._state_lock:
                    self._log = (self._log + [text])[-80:]
            return
        if request_id is not None and method:
            result: Any = None
            if method == "workspace/configuration":
                items = (message.get("params") or {}).get("items") or []
                result = [{} for _item in items]
            elif method == "workspace/workspaceFolders":
                result = [{"uri": self.root.as_uri(), "name": self.root.name}]
            elif method == "workspace/applyEdit":
                result = {"applied": False}
            try:
                self._write({"jsonrpc": "2.0", "id": request_id, "result": result})
            except (OSError, RuntimeError):
                return

    def _fail_pending(self, message: str) -> None:
        with self._state_lock:
            for pending in self._pending.values():
                pending["error"] = {"message": message}
                pending["event"].set()

    def sync(self, path: Path, content: str, version: int) -> str:
        self.ensure_started()
        uri = path.resolve(strict=False).as_uri()
        with self._state_lock:
            previous = self._opened_versions.get(uri)
            version_number = max(
                1,
                int(version or 1),
                (previous + 1) if previous is not None else 1,
            )
            self._opened_versions[uri] = version_number
        if previous is None:
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": (
                            "bibtex"
                            if path.suffix.casefold() == ".bib"
                            else "latex"
                        ),
                        "version": version_number,
                        "text": str(content or ""),
                    }
                },
            )
        else:
            self.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version_number},
                    "contentChanges": [{"text": str(content or "")}],
                },
            )
        return uri

    def diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        with self._state_lock:
            return {
                uri: [dict(item) for item in items]
                for uri, items in self._diagnostics.items()
            }

    def status(self) -> dict[str, Any]:
        return {
            "available": self.alive and self._initialized,
            "pid": self.process.pid if self.alive and self.process else None,
            "uptime_ms": (
                int((time.monotonic() - self._started_at) * 1000)
                if self.alive and self._started_at
                else 0
            ),
            "opened_documents": len(self._opened_versions),
            "log": list(self._log[-12:]),
        }

    def _terminate(self) -> None:
        with self._state_lock:
            process = self.process
            self.process = None
            self._initialized = False
            self._opened_versions.clear()
            self._fail_pending("TexLab connection closed")
        if process is None:
            return
        try:
            # A dead leader can still have build tools holding its pipes open.
            process_runtime.terminate_process_tree(process, timeout=1.5, force=True)
        except BaseException:
            self.process = process  # Retain ownership so cleanup can be retried.
            raise
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def close(self) -> None:
        with self._start_lock:
            if self.alive and self._initialized:
                try:
                    self.request("shutdown", {}, timeout=1.5, start=False)
                    self.notify("exit", {}, start=False)
                except (OSError, RuntimeError, TimeoutError):
                    pass
            self._terminate()


def _manager(article_id: str, root: Path) -> TexLabSession:
    with _TEXLAB_MANAGERS_LOCK:
        manager = _TEXLAB_MANAGERS.get(article_id)
        if manager is not None and manager.root != root.resolve(strict=False):
            manager.close()
            manager = None
        if manager is None:
            manager = TexLabSession(article_id, root)
            _TEXLAB_MANAGERS[article_id] = manager
        return manager


def _uri_to_relative(root: Path, uri: str) -> str:
    parsed = urlparse(str(uri or ""))
    if parsed.scheme == "file":
        authority = f"//{parsed.netloc}" if parsed.netloc and parsed.netloc.casefold() != "localhost" else ""
        raw_path = url2pathname(authority + parsed.path)
    else:
        raw_path = str(uri or "")
    target = Path(raw_path).resolve(strict=False)
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        return ""


def _client_range(value: Any) -> dict[str, int]:
    payload = value if isinstance(value, dict) else {}
    start = payload.get("start") if isinstance(payload.get("start"), dict) else {}
    end = payload.get("end") if isinstance(payload.get("end"), dict) else start
    return {
        "line": int(start.get("line") or 0) + 1,
        "column": int(start.get("character") or 0) + 1,
        "end_line": int(end.get("line") or 0) + 1,
        "end_column": int(end.get("character") or 0) + 1,
    }


def _client_locations(root: Path, value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else [value]
    output: list[dict[str, Any]] = []
    for raw in raw_items:
        item = raw if isinstance(raw, dict) else {}
        uri = str(item.get("uri") or item.get("targetUri") or "")
        path = _uri_to_relative(root, uri)
        range_payload = item.get("range") or item.get("targetSelectionRange")
        if not path or not isinstance(range_payload, dict):
            continue
        output.append({"path": path, **_client_range(range_payload)})
    return output


def _client_symbols(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    output: list[dict[str, Any]] = []
    for raw in raw_items:
        item = raw if isinstance(raw, dict) else {}
        range_payload = item.get("selectionRange") or item.get("range")
        if not isinstance(range_payload, dict):
            continue
        output.append(
            {
                "name": str(item.get("name") or ""),
                "detail": str(item.get("detail") or ""),
                "kind": int(item.get("kind") or 0),
                **_client_range(range_payload),
                "children": _client_symbols(item.get("children")),
            }
        )
    return output


def _client_diagnostics(
    root: Path, diagnostics: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    severity_names = {1: "error", 2: "warning", 3: "info", 4: "hint"}
    for uri, items in diagnostics.items():
        path = _uri_to_relative(root, uri)
        if not path:
            continue
        for raw in items:
            item = raw if isinstance(raw, dict) else {}
            severity = int(item.get("severity") or 3)
            output.append(
                {
                    "source": str(item.get("source") or "texlab"),
                    "code": str(item.get("code") or ""),
                    "severity": severity_names.get(severity, "info"),
                    "message": str(item.get("message") or ""),
                    "path": path,
                    **_client_range(item.get("range")),
                }
            )
    return output


def article_texlab_request(
    article_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("TexLab payload must be an object")
    _record, _root_item, root = article_identity_backend._article_source_context(
        article_id
    )
    manager = _manager(article_id, root)
    action = str(payload.get("action") or "status").strip().casefold()
    if action == "status":
        try:
            manager.ensure_started()
            return {
                "id": article_id,
                "service": "texlab",
                **manager.status(),
            }
        except (OSError, RuntimeError, TimeoutError) as exc:
            return {
                "id": article_id,
                "service": "texlab",
                "available": False,
                "error": str(exc),
            }

    raw_path = str(payload.get("path") or "").strip()
    if not raw_path:
        raise ValueError("TexLab document path required")
    _article_root, path = article_identity_backend._article_target_path(
        article_id, raw_path
    )
    if path.suffix.casefold() not in {".tex", ".bib", ".sty", ".cls"}:
        raise ValueError("TexLab supports LaTeX and bibliography source files")
    if not path.is_file():
        raise FileNotFoundError(path)
    content = str(payload.get("content") or "")
    version = max(1, int(payload.get("version") or 1))
    uri = manager.sync(path, content, version)
    position = {
        "line": max(0, int(payload.get("line") or 0)),
        "character": max(0, int(payload.get("character") or 0)),
    }
    document = {"uri": uri}

    if action == "sync":
        time.sleep(0.04)
        return {
            "id": article_id,
            "service": "texlab",
            "available": True,
            "diagnostics": _client_diagnostics(root, manager.diagnostics()),
        }
    if action == "diagnostics":
        return {
            "id": article_id,
            "service": "texlab",
            "available": True,
            "diagnostics": _client_diagnostics(root, manager.diagnostics()),
        }
    if action == "completion":
        result = manager.request(
            "textDocument/completion",
            {
                "textDocument": document,
                "position": position,
                "context": {
                    "triggerKind": int(payload.get("trigger_kind") or 1),
                    **(
                        {"triggerCharacter": str(payload.get("trigger_character"))}
                        if payload.get("trigger_character")
                        else {}
                    ),
                },
            },
        )
        items = result.get("items") if isinstance(result, dict) else result
        return {
            "id": article_id,
            "service": "texlab",
            "available": True,
            "is_incomplete": bool(
                result.get("isIncomplete") if isinstance(result, dict) else False
            ),
            "items": items if isinstance(items, list) else [],
        }
    if action == "hover":
        return {
            "id": article_id,
            "service": "texlab",
            "available": True,
            "hover": manager.request(
                "textDocument/hover",
                {"textDocument": document, "position": position},
            ),
        }
    if action == "definition":
        result = manager.request(
            "textDocument/definition",
            {"textDocument": document, "position": position},
        )
        return {
            "id": article_id,
            "service": "texlab",
            "available": True,
            "locations": _client_locations(root, result),
        }
    if action == "document_symbols":
        result = manager.request(
            "textDocument/documentSymbol", {"textDocument": document}
        )
        return {
            "id": article_id,
            "service": "texlab",
            "available": True,
            "symbols": _client_symbols(result),
        }
    raise ValueError(f"unsupported TexLab action: {action}")


def _close_all() -> None:
    with _TEXLAB_MANAGERS_LOCK:
        managers = list(_TEXLAB_MANAGERS.values())
        _TEXLAB_MANAGERS.clear()
    for manager in managers:
        manager.close()


atexit.register(_close_all)
