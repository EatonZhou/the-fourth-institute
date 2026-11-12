from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


from .runtime import setting


class TelegramConfigError(RuntimeError):
    pass


def _http_error_preview(error: urllib.error.HTTPError) -> str:
    # Preserve the 1000-character diagnostic without buffering an arbitrary
    # upstream error page. UTF-8 needs at most four bytes per character.
    with error:
        return error.read(4000).decode("utf-8", errors="replace")[:1000]


def load_bot_token() -> str:
    from management_common.instance_manifest import load
    path = load().directory('secrets') / 'telepus_telegram_bot.key'
    try:
        return path.read_text(encoding='utf-8').strip()
    except OSError:
        raise TelegramConfigError('Instance Telegram bot secret is unavailable') from None


class TelegramClient:
    def __init__(
        self,
        token: str | None = None,
        timeout_seconds: int = 30,
        upload_timeout_seconds: int | None = None,
        upload_interface: str | None = None,
    ):
        self.token = token or load_bot_token()
        self.timeout_seconds = int(timeout_seconds)
        configured_upload_timeout = int(os.getenv("TELEPUS_TELEGRAM_UPLOAD_TIMEOUT_SECONDS", "180"))
        self.upload_timeout_seconds = int(upload_timeout_seconds or configured_upload_timeout)
        self.upload_interface = str(
            upload_interface if upload_interface is not None else setting("telegram_upload_interface", "")
        ).strip()

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe")

    def get_webhook_info(self) -> dict[str, Any]:
        return self.call("getWebhookInfo")

    def get_updates(self, *, offset: int | None = None, limit: int = 20, timeout: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": int(limit), "timeout": int(timeout)}
        if offset is not None:
            params["offset"] = int(offset)
        payload = self.call("getUpdates", params)
        result = payload.get("result")
        return result if isinstance(result, list) else []

    def get_file(self, file_id: str) -> dict[str, Any]:
        payload = self.call("getFile", {"file_id": str(file_id)})
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def download_file(self, file_path: str, *, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        clean_path = str(file_path or "").strip().lstrip("/")
        if not clean_path:
            raise RuntimeError("telegram file_path missing")
        url = f"https://api.telegram.org/file/bot{self.token}/{urllib.parse.quote(clean_path, safe='/')}"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = response.read(max(1, int(max_bytes)) + 1)
        except urllib.error.HTTPError as exc:
            body = _http_error_preview(exc)
            raise RuntimeError(f"telegram file download failed: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"telegram file download failed: {exc.reason}") from exc
        if len(data) > int(max_bytes):
            raise RuntimeError("telegram file exceeds configured image size limit")
        return data

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": str(text),
            "disable_web_page_preview": True,
        }
        if parse_mode:
            params["parse_mode"] = str(parse_mode)
        if reply_to_message_id:
            params["reply_to_message_id"] = int(reply_to_message_id)
            params["allow_sending_without_reply"] = True
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.call("sendMessage", params)

    def send_document(
        self,
        *,
        chat_id: str,
        filename: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        return self.call_multipart(
            "sendDocument",
            {"chat_id": str(chat_id)},
            file_field="document",
            filename=filename,
            file_data=data,
            content_type=content_type,
            progress_callback=progress_callback,
        )

    def send_photo(
        self,
        *,
        chat_id: str,
        filename: str,
        data: bytes,
        content_type: str = "image/jpeg",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        return self.call_multipart(
            "sendPhoto",
            {"chat_id": str(chat_id)},
            file_field="photo",
            filename=filename,
            file_data=data,
            content_type=content_type,
            progress_callback=progress_callback,
        )

    def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "text": str(text),
            "disable_web_page_preview": True,
        }
        if parse_mode:
            params["parse_mode"] = str(parse_mode)
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        return self.call("editMessageText", params)

    def answer_callback_query(self, *, callback_query_id: str, text: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"callback_query_id": str(callback_query_id)}
        if text:
            params["text"] = str(text)
        return self.call("answerCallbackQuery", params)

    def set_my_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        return self.call("setMyCommands", {"commands": json.dumps(commands, ensure_ascii=False)})

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(params or {}).encode("utf-8")
        request = urllib.request.Request(url, data=data if params is not None else None, method="POST")
        return self._execute(request, method)

    def call_multipart(
        self,
        method: str,
        params: dict[str, Any],
        *,
        file_field: str,
        filename: str,
        file_data: bytes,
        content_type: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if self.upload_interface:
            return self._call_multipart_curl(
                method,
                params,
                file_field=file_field,
                filename=filename,
                file_data=file_data,
                content_type=content_type,
                progress_callback=progress_callback,
            )
        boundary = f"telepus-{secrets.token_hex(12)}"
        boundary_bytes = boundary.encode("ascii")
        body: list[bytes] = []
        for name, value in params.items():
            body.extend([
                b"--" + boundary_bytes + b"\r\n",
                f'Content-Disposition: form-data; name="{str(name)}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        safe_filename = Path(str(filename or "attachment")).name.replace("\r", "").replace("\n", "").replace('"', "") or "attachment"
        body.extend([
            b"--" + boundary_bytes + b"\r\n",
            f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {str(content_type or 'application/octet-stream')}\r\n\r\n".encode("ascii", errors="replace"),
        ])
        prefix = b"".join(body)
        payload = bytes(file_data)
        suffix = b"\r\n--" + boundary_bytes + b"--\r\n"
        request_data: bytes | Iterable[bytes]
        if progress_callback:
            request_data = self._multipart_stream(
                prefix,
                payload,
                suffix,
                progress_callback=progress_callback,
            )
        else:
            request_data = prefix + payload + suffix
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=request_data,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(prefix) + len(payload) + len(suffix)),
            },
            method="POST",
        )
        setattr(request, "_telepus_timeout_seconds", self.upload_timeout_seconds)
        return self._execute(request, method)

    def _call_multipart_curl(
        self,
        method: str,
        params: dict[str, Any],
        *,
        file_field: str,
        filename: str,
        file_data: bytes,
        content_type: str,
        progress_callback: Callable[[int, int], None] | None,
    ) -> dict[str, Any]:
        safe_filename = (
            Path(str(filename or "attachment")).name
            .replace("\r", "")
            .replace("\n", "")
            .replace('"', "")
            .replace(";", "")
        ) or "attachment"
        suffix = Path(safe_filename).suffix[:16]
        with tempfile.NamedTemporaryFile(prefix="telepus-upload-", suffix=suffix) as upload_file:
            upload_file.write(bytes(file_data))
            upload_file.flush()
            lines = [self._curl_config_line("url", f"https://api.telegram.org/bot{self.token}/{method}")]
            for name, value in params.items():
                lines.append(self._curl_config_line("form-string", f"{name}={value}"))
            lines.extend([
                self._curl_config_line(
                    "form",
                    f"{file_field}=@{upload_file.name};type={content_type};filename={safe_filename}",
                ),
                f"connect-timeout = {min(30, self.upload_timeout_seconds)}",
                f"max-time = {self.upload_timeout_seconds}",
                self._curl_config_line("interface", self.upload_interface),
                "silent",
                "show-error",
            ])
            if progress_callback:
                progress_callback(0, len(file_data))
            try:
                completed = subprocess.run(
                    ["curl", "--config", "-"],
                    input=("\n".join(lines) + "\n").encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=self.upload_timeout_seconds + 5,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"telegram {method} failed: upload timed out") from exc
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
            raise RuntimeError(f"telegram {method} failed: curl exit {completed.returncode}: {error}")
        try:
            payload = json.loads(completed.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"telegram {method} returned invalid JSON") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError(f"telegram {method} returned non-ok response: {str(payload)[:1000]}")
        if progress_callback:
            progress_callback(len(file_data), len(file_data))
        return payload

    @staticmethod
    def _curl_config_line(name: str, value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
        return f'{name} = "{escaped}"'

    @staticmethod
    def _multipart_stream(
        prefix: bytes,
        payload: bytes,
        suffix: bytes,
        *,
        progress_callback: Callable[[int, int], None],
        chunk_size: int = 256 * 1024,
    ) -> Iterable[bytes]:
        total = len(payload)
        progress_callback(0, total)
        yield prefix
        sent = 0
        view = memoryview(payload)
        for offset in range(0, total, max(1, int(chunk_size))):
            chunk = bytes(view[offset:offset + max(1, int(chunk_size))])
            yield chunk
            sent += len(chunk)
            progress_callback(sent, total)
        yield suffix

    def _execute(self, request: urllib.request.Request, method: str) -> dict[str, Any]:
        timeout_seconds = int(getattr(request, "_telepus_timeout_seconds", self.timeout_seconds))
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = _http_error_preview(exc)
            raise RuntimeError(f"telegram {method} failed: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"telegram {method} failed: {exc.reason}") from exc
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise RuntimeError(f"telegram {method} returned non-ok response: {str(payload)[:1000]}")
        return payload
