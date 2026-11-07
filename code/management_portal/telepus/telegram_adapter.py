from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from management_common.portal_paths import path as portal_path
from .router_core import RouterCore
from .telegram_client import TelegramClient
from .telegram_format import markdown_to_telegram_html, split_telegram_text

DEFAULT_TELEPUS_TELEGRAM_IMAGE_ROOT = portal_path('uploads') / 'resident_chat_images'
TELEPUS_TELEGRAM_IMAGE_MAX_BYTES = int(os.getenv("TELEPUS_TELEGRAM_IMAGE_MAX_BYTES", "20971520"))
TELEGRAM_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class TelegramAdapter:
    """Telegram updates in, RouterCore responses out."""

    def __init__(self, router: RouterCore, client: TelegramClient | None = None, *,
                 background_attachment_uploads: bool = False):
        self.router = router
        self.client = client
        self.background_attachment_uploads = background_attachment_uploads
        # The error each chat last recorded; a chat failing the same way on
        # every pass is audited once, until it recovers or the error changes.
        self._refresh_errors: dict[str, tuple[str, str]] = {}

    # -- loops -------------------------------------------------------------------------

    def poll_once(self, *, limit: int = 20, timeout: int = 0, dry_run: bool = False) -> dict[str, Any]:
        if dry_run:
            raise ValueError("poll_once dry_run still needs Telegram network; use handle_update_file for local tests")
        client = self.client or TelegramClient()
        offset_raw = self.router.store.get_meta("telegram_update_offset", "")
        offset = int(offset_raw) + 1 if offset_raw.isdigit() else None
        updates = client.get_updates(offset=offset, limit=limit, timeout=timeout)
        handled = [self.handle_update(update, dry_run=False) for update in updates]
        if updates:
            max_update_id = max(int(item.get("update_id") or 0) for item in updates)
            if max_update_id:
                self.router.store.set_meta("telegram_update_offset", str(max_update_id))
        return {"schema": "telepus.telegram.poll_once.v1", "updates": len(updates), "handled": handled}

    def refresh_routes(self, *, limit: int = 100, dry_run: bool = False) -> dict[str, Any]:
        """Push what arrived for every chat that has sent to Portal."""
        states = self.router.store.watched_chats(limit=limit)
        handled = []
        for state in states:
            chat_id = state["chat_id"]
            if not self._still_acts_as(state):
                # The contact was removed or rebound; Portal would be read as a deleted or different User.
                handled.append({"chat_id": chat_id, "status": "unbound"})
                continue
            try:
                outgoing = self.router.refresh_chat(state)
                result = self._send(chat_id, outgoing, dry_run=dry_run, status="refreshed") if outgoing else \
                    {"chat_id": chat_id, "status": "no_new_message"}
            except Exception as exc:  # noqa: BLE001 - one chat never stops the others
                unsent = getattr(exc, "unsent_items", None)
                if unsent:
                    self.router.restore_unsent(state, unsent)
                error = (type(exc).__name__, str(exc))
                if self._refresh_errors.get(chat_id) != error:
                    self._refresh_errors[chat_id] = error
                    self.router.store.append_audit(
                        "refresh_error", chat_id=chat_id, telegram_user_id=state["telegram_user_id"],
                        portal_actor=state["portal_actor"], payload={"error_type": error[0], "error": error[1]})
                handled.append({"chat_id": chat_id, "status": "error", "error_type": error[0], "error": error[1]})
                continue
            self._refresh_errors.pop(chat_id, None)
            handled.append(result)
        return {"schema": "telepus.telegram.refresh_routes.v2", "chats": len(states),
                "errors": sum(1 for item in handled if item.get("status") == "error"), "handled": handled}

    # -- updates -----------------------------------------------------------------------

    def handle_update_file(self, path: Path, *, dry_run: bool = True) -> dict[str, Any]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        updates = payload if isinstance(payload, list) else [payload]
        handled = [self.handle_update(update, dry_run=dry_run) for update in updates if isinstance(update, dict)]
        return {"schema": "telepus.telegram.handle_update_file.v1", "updates": len(handled), "handled": handled}

    def handle_update(self, update: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        update_id = int(update.get("update_id") or 0)
        if update_id and not self.router.store.mark_update_processed(update_id):
            return {"update_id": update_id, "status": "duplicate"}
        if isinstance(update.get("message"), dict):
            return self._handle_message(update_id, update["message"], dry_run=dry_run)
        if isinstance(update.get("callback_query"), dict):
            return self._handle_callback(update_id, update["callback_query"], dry_run=dry_run)
        return {"update_id": update_id, "status": "ignored", "reason": "unsupported update type"}

    def _handle_message(self, update_id: int, message: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        user = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat_id = str(chat.get("id") or "")
        user_id = str(user.get("id") or "")
        message_id = int(message.get("message_id") or 0)
        base_text = self._message_base_text(message)
        has_image = bool(self._message_image_candidates(message))
        if not chat_id or not user_id or (not base_text and not has_image):
            return {"update_id": update_id, "status": "ignored", "reason": "missing chat, user or routable content"}
        actor = self._actor_for_user(user_id)
        if not actor:
            return self._send(chat_id, [{"text": self._unauthorized_text(user_id), "reply_to_telegram_message_id": message_id}],
                              dry_run=dry_run, status="unauthorized", update_id=update_id)
        text, image_errors = self._message_text_with_images(message, actor=actor)
        if not text:
            lines = ["这条 Telegram 消息没有文字，图片也未能下载。", *image_errors]
            return self._send(chat_id, [{"text": "\n".join(lines), "reply_to_telegram_message_id": message_id}],
                              dry_run=dry_run, status="image_download_failed", update_id=update_id)
        replied = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
        response = self.router.dispatch_message(
            chat_id=chat_id, telegram_user_id=user_id, text=text, portal_actor=actor,
            chat_type=str(chat.get("type") or "private"), telegram_message_id=message_id,
            reply_to_telegram_message_id=int(replied.get("message_id") or 0),
        )
        return self._respond(chat_id, response, dry_run=dry_run, update_id=update_id, reply_to=message_id)

    def _handle_callback(self, update_id: int, callback: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        callback_id = str(callback.get("id") or "")
        user = callback.get("from") if isinstance(callback.get("from"), dict) else {}
        user_id = str(user.get("id") or "")
        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "")
        actor = self._actor_for_user(user_id)
        if not actor:
            return self._send(chat_id, [{"text": self._unauthorized_text(user_id)}], dry_run=dry_run,
                              status="unauthorized", update_id=update_id)
        response = self.router.dispatch_callback(
            chat_id=chat_id, telegram_user_id=user_id, data=str(callback.get("data") or ""), portal_actor=actor,
            chat_type=str(chat.get("type") or "private"), telegram_message_id=int(message.get("message_id") or 0),
        )
        attachment = response.get("attachment_delivery") if isinstance(response.get("attachment_delivery"), dict) else None
        if not dry_run and self.client and callback_id:
            try:
                self.client.answer_callback_query(callback_query_id=callback_id,
                                                  text="正在发送文件" if attachment else "")
            except Exception:  # noqa: BLE001 - an expired callback still gets its result
                pass
        if attachment:
            return self._deliver_attachment(chat_id, attachment, dry_run=dry_run, update_id=update_id)
        return self._respond(chat_id, response, dry_run=dry_run, update_id=update_id)

    # -- sending -------------------------------------------------------------------------

    def _respond(self, chat_id: str, response: dict[str, Any], *, dry_run: bool, update_id: int,
                 reply_to: int = 0) -> dict[str, Any]:
        outgoing = [dict(item) for item in response.get("messages") or [] if isinstance(item, dict)]
        if reply_to:
            for item in outgoing:
                item.setdefault("reply_to_telegram_message_id", reply_to)
        edit = response.get("edit") if isinstance(response.get("edit"), dict) else None
        result: dict[str, Any] = {"update_id": update_id, "chat_id": chat_id, "status": "handled"}
        if edit:
            result["edit"] = self._edit(chat_id, edit, dry_run=dry_run)
        if outgoing or not edit:
            try:
                sent = self._send(chat_id, outgoing or [{"text": "收到。"}], dry_run=dry_run, status="handled",
                                  update_id=update_id)
            except Exception as exc:
                unsent = getattr(exc, "unsent_items", None)
                if unsent:
                    self.router.restore_unsent(self.router.store.get_chat_state(chat_id), unsent)
                raise
            result.update({key: value for key, value in sent.items() if key not in result})
        return result

    def _edit(self, chat_id: str, edit: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        message_id = int(edit.get("telegram_message_id") or 0)
        markup = edit.get("reply_markup") if isinstance(edit.get("reply_markup"), dict) else None
        if dry_run or not self.client:
            return {"telegram_message_id": message_id, "text": edit.get("text"), "reply_markup": markup or {}}
        if message_id:
            try:
                self.client.edit_message_text(chat_id=chat_id, message_id=message_id,
                                              text=markdown_to_telegram_html(str(edit.get("text") or "")),
                                              reply_markup=markup, parse_mode="HTML")
                return {"telegram_message_id": message_id, "edited": True}
            except RuntimeError:
                pass
        # The message to edit is gone or unknown: show the new state as a new message.
        return self._send(chat_id, [edit], dry_run=False, status="edit_resent")

    def _send(self, chat_id: str, outgoing: list[dict[str, Any]], *, dry_run: bool, status: str,
              update_id: int = 0) -> dict[str, Any]:
        items = [item for item in outgoing if str(item.get("text") or "").strip()]
        if dry_run or not self.client:
            return {"update_id": update_id, "status": status, "chat_id": chat_id, "parse_mode": "HTML",
                    "dry_run_messages": [item["text"] for item in items],
                    "dry_run_items": [{key: value for key, value in item.items()} for item in items]}
        sent_count = 0
        for position, item in enumerate(items):
            pieces = split_telegram_text(str(item["text"]))
            link = item.get("link") if isinstance(item.get("link"), dict) else None
            reply_to = int(item.get("reply_to_telegram_message_id") or 0)
            last_id = 0
            try:
                for index, piece in enumerate(pieces):
                    markup = item.get("reply_markup") if index == len(pieces) - 1 else None
                    result = self._send_chunk(chat_id=chat_id, text=piece,
                                              reply_to_message_id=reply_to if index == 0 else 0,
                                              reply_markup=markup if isinstance(markup, dict) else None)
                    last_id = int(((result or {}).get("result") or {}).get("message_id") or 0)
                    if link:
                        self.router.store.link_message(chat_id, last_id, link["resident_id"], link["message_id"])
                    sent_count += 1
            except Exception as exc:
                # The caller puts these back so the next refresh sends them again.
                exc.unsent_items = items[position:]  # type: ignore[attr-defined]
                raise
            if item.get("question_session") and last_id:
                self.router.store.update_question_session(str(item["question_session"]), telegram_message_id=last_id)
        return {"update_id": update_id, "status": status, "chat_id": chat_id, "sent_count": sent_count}

    def _send_chunk(self, *, chat_id: str, text: str, reply_to_message_id: int = 0,
                    reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("telegram client required")
        try:
            return self.client.send_message(chat_id=chat_id, text=markdown_to_telegram_html(text),
                                            reply_to_message_id=reply_to_message_id or None,
                                            reply_markup=reply_markup, parse_mode="HTML")
        except RuntimeError as exc:
            if not is_telegram_format_error(exc):
                raise
            return self.client.send_message(chat_id=chat_id, text=text,
                                            reply_to_message_id=reply_to_message_id or None, reply_markup=reply_markup)

    # -- attachments ------------------------------------------------------------------------

    def _deliver_attachment(self, chat_id: str, attachment: dict[str, Any], *, dry_run: bool, update_id: int) -> dict[str, Any]:
        token = str(attachment.get("token") or "")
        if dry_run or not self.client:
            return self._send_attachment(chat_id=chat_id, attachment=attachment, dry_run=True, update_id=update_id)
        if not self.router.start_attachment_delivery(token):
            return self._send(chat_id, [{"text": "文件正在发送，请稍候。"}], dry_run=False,
                              status="attachment_sending", update_id=update_id)
        if self.background_attachment_uploads:
            threading.Thread(target=self._send_attachment_in_background,
                             kwargs={"chat_id": chat_id, "attachment": attachment, "update_id": update_id},
                             name=f"telepus-attachment-{token[:8]}", daemon=True).start()
            return {"update_id": update_id, "status": "attachment_queued", "chat_id": chat_id,
                    "attachment": {"token": token, "filename": str(attachment.get("filename") or "attachment")}}
        return self._send_attachment(chat_id=chat_id, attachment=attachment, dry_run=False, update_id=update_id)

    def _send_attachment_in_background(self, *, chat_id: str, attachment: dict[str, Any], update_id: int) -> None:
        token = str(attachment.get("token") or "")
        try:
            self._send_attachment(chat_id=chat_id, attachment=attachment, dry_run=False, update_id=update_id)
        except Exception as exc:  # noqa: BLE001
            self.router.fail_attachment_delivery(token, f"{type(exc).__name__}: {str(exc)[:500]}")
            if self.client:
                try:
                    self.client.send_message(chat_id=chat_id, text="文件发送失败，请稍后重试。")
                except Exception:  # noqa: BLE001
                    pass

    def _send_attachment(self, *, chat_id: str, attachment: dict[str, Any], dry_run: bool, update_id: int) -> dict[str, Any]:
        token = str(attachment.get("token") or "")
        filename = str(attachment.get("filename") or "attachment")
        content_type = str(attachment.get("content_type") or "application/octet-stream").split(";", 1)[0].strip().lower()
        data = attachment.get("data")
        if not isinstance(data, bytes):
            self.router.fail_attachment_delivery(token, "attachment bytes missing")
            return self._send(chat_id, [{"text": "文件发送失败：附件内容缺失。"}], dry_run=dry_run,
                              status="attachment_failed", update_id=update_id)
        summary = {"token": token, "filename": filename, "content_type": content_type, "size": len(data)}
        if dry_run or not self.client:
            return {"update_id": update_id, "status": "attachment_preview", "chat_id": chat_id, "dry_run_attachment": summary}
        progress_message_id = 0
        try:
            progress = self.client.send_message(chat_id=chat_id, text=self._attachment_progress_text(filename, 0, len(data)))
            progress_message_id = int(((progress or {}).get("result") or {}).get("message_id") or 0)
        except Exception:  # noqa: BLE001
            progress_message_id = 0
        progress_state = {"percent": -10, "updated_at": 0.0}

        def update_progress(sent_bytes: int, total_bytes: int) -> None:
            if not progress_message_id:
                return
            percent = 100 if total_bytes <= 0 else min(100, int(sent_bytes * 100 / total_bytes))
            now = time.monotonic()
            if percent < 100 and (percent < progress_state["percent"] + 10 or now - progress_state["updated_at"] < 1.5):
                return
            try:
                self.client.edit_message_text(chat_id=chat_id, message_id=progress_message_id,
                                              text=self._attachment_progress_text(filename, sent_bytes, total_bytes))
            except Exception:  # noqa: BLE001
                return
            progress_state.update(percent=percent, updated_at=now)

        def finish(text: str) -> None:
            if progress_message_id:
                try:
                    self.client.edit_message_text(chat_id=chat_id, message_id=progress_message_id, text=text)
                except Exception:  # noqa: BLE001
                    pass

        try:
            send = self.client.send_photo if content_type in {"image/jpeg", "image/png", "image/webp"} else self.client.send_document
            sent = send(chat_id=chat_id, filename=filename, data=data, content_type=content_type,
                        progress_callback=update_progress)
        except Exception as exc:  # noqa: BLE001
            self.router.fail_attachment_delivery(token, f"{type(exc).__name__}: {str(exc)[:500]}")
            finish(f"文件发送失败：{filename}")
            return self._send(chat_id, [{"text": f"文件发送失败：{type(exc).__name__}: {str(exc)[:240]}"}],
                              dry_run=False, status="attachment_failed", update_id=update_id)
        self.router.ack_attachment_delivery(token, sent)
        finish(f"文件发送完成：{filename}")
        return {"update_id": update_id, "status": "attachment_sent", "chat_id": chat_id, "attachment": summary}

    @staticmethod
    def _attachment_progress_text(filename: str, sent_bytes: int, total_bytes: int) -> str:
        percent = 100 if total_bytes <= 0 else min(100, int(sent_bytes * 100 / total_bytes))
        return (f"正在上传：{filename}\n{percent}% · {max(0, int(sent_bytes)) / 1048576:.1f}/"
                f"{max(0, int(total_bytes)) / 1048576:.1f} MB")

    # -- contacts --------------------------------------------------------------------------

    def _actor_for_user(self, user_id: str) -> str:
        # Portal's Telepus contacts are the bindings (decision_692a484e9bea).
        return self.router.store.contact_actor(user_id)

    def _still_acts_as(self, state: dict[str, Any]) -> bool:
        actor = self._actor_for_user(str(state.get("telegram_user_id") or ""))
        return bool(actor) and actor.casefold() == str(state.get("portal_actor") or "").strip().casefold()

    @staticmethod
    def _unauthorized_text(user_id: str) -> str:
        return "\n".join([
            "未授权使用 Telepus。",
            f"你的 Telegram 数字 ID 是 `{user_id}`。",
            "请登录 Portal，在 Telepus → Contacts 绑定本人；或请管理员代为绑定。",
        ])

    # -- Telegram images -----------------------------------------------------------------------

    @staticmethod
    def _message_base_text(message: dict[str, Any]) -> str:
        return str(message.get("text") or message.get("caption") or "").strip()

    def _message_text_with_images(self, message: dict[str, Any], *, actor: str) -> tuple[str, list[str]]:
        text = self._message_base_text(message)
        image_refs: list[str] = []
        errors: list[str] = []
        for index, candidate in enumerate(self._message_image_candidates(message)):
            try:
                saved = self._download_message_image(candidate, actor=actor, message=message, index=index)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Telegram 图片下载失败：{type(exc).__name__}: {str(exc)[:240]}")
                continue
            path = str(saved.get("path") or "").strip()
            if path:
                image_refs.append(f"[image: {path}]")
        parts = [text] if text else []
        parts.extend(image_refs)
        if errors and text:
            parts.extend(f"[Telegram image unavailable: {item}]" for item in errors)
        return "\n\n".join(part for part in parts if part).strip(), errors

    @staticmethod
    def _message_image_candidates(message: dict[str, Any]) -> list[dict[str, Any]]:
        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            candidates = [item for item in photo if isinstance(item, dict) and str(item.get("file_id") or "").strip()]
            if candidates:
                best = max(candidates, key=lambda item: (int(item.get("file_size") or 0),
                                                         int(item.get("width") or 0) * int(item.get("height") or 0)))
                return [{"kind": "photo", **best}]
        document = message.get("document") if isinstance(message.get("document"), dict) else {}
        mime_type = str(document.get("mime_type") or "").split(";", 1)[0].strip().lower()
        if str(document.get("file_id") or "").strip() and mime_type.startswith("image/"):
            return [{"kind": "document", **document}]
        return []

    def _download_message_image(self, candidate: dict[str, Any], *, actor: str, message: dict[str, Any],
                                index: int) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("telegram client unavailable")
        file_id = str(candidate.get("file_id") or "").strip()
        if not file_id:
            raise RuntimeError("telegram image file_id missing")
        if int(candidate.get("file_size") or 0) > TELEPUS_TELEGRAM_IMAGE_MAX_BYTES:
            raise RuntimeError("telegram image exceeds configured size limit")
        file_info = self.client.get_file(file_id)
        file_path = str(file_info.get("file_path") or "").strip()
        data = self.client.download_file(file_path, max_bytes=TELEPUS_TELEGRAM_IMAGE_MAX_BYTES)
        suffix = _image_suffix(file_path, str(candidate.get("mime_type") or ""), data)
        now = datetime.now(timezone.utc)
        message_id = int(message.get("message_id") or 0)
        unique = str(candidate.get("file_unique_id") or file_info.get("file_unique_id") or file_id)
        digest = hashlib.sha256(f"{unique}:{len(data)}:{hashlib.sha256(data).hexdigest()}".encode("utf-8")).hexdigest()[:12]
        file_name = f"{int(now.timestamp())}_{digest}_telegram_{message_id}_{index}{suffix}"
        rel_path = Path(_safe_path_part(actor)) / now.strftime("%Y%m%d") / file_name
        root = DEFAULT_TELEPUS_TELEGRAM_IMAGE_ROOT.resolve(strict=False)
        abs_path = (root / rel_path).resolve(strict=False)
        abs_path.relative_to(root)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = abs_path.with_name(f".{abs_path.name}.{secrets.token_hex(4)}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, abs_path)
        finally:
            tmp.unlink(missing_ok=True)
        meta = {
            "created_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "actor": str(actor or ""), "source": "telegram", "telegram_message_id": message_id,
            "telegram_media_kind": str(candidate.get("kind") or ""),
            "telegram_file_unique_id": str(candidate.get("file_unique_id") or ""),
            "telegram_file_path": file_path, "filename": file_name, "content_type": _content_type_for_suffix(suffix),
            "size": len(data), "path": str(abs_path), "file_id": rel_path.as_posix(),
        }
        abs_path.with_suffix(abs_path.suffix + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                                                   encoding="utf-8")
        return meta


def is_telegram_format_error(exc: RuntimeError) -> bool:
    text = str(exc).lower()
    return "can't parse" in text or "entity" in text or "parse_mode" in text


def _safe_path_part(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return text[:64] or "user"


def _image_suffix(file_path: str, mime_type: str, data: bytes) -> str:
    suffix = Path(str(file_path or "")).suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    if suffix in TELEGRAM_IMAGE_SUFFIXES:
        return suffix
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    by_mime = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
    if mime in by_mime:
        return by_mime[mime]
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise RuntimeError("telegram image type unsupported")


def _content_type_for_suffix(suffix: str) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            ".gif": "image/gif"}.get(str(suffix or "").lower(), "application/octet-stream")
