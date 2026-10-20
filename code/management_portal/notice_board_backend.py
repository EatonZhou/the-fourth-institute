from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


from . import conversation_read_state
from .mailbox_sources import MailboxSource
from .notifications import publish_local_projection_change
from .notice_store import NoticeState


# Images pasted into posts before post images became uploads, kept in the
# resource registry as ``{"content_type", "data_base64"}`` documents.
NOTICE_BOARD_IMAGE_COLLECTION = "notice_board_image"
NOTICE_BOARD_SCHEMA = "tfi.portal.notice_board.v1"
NOTICE_BOARD_MODERATION_SCHEMA = "tfi.portal.notice_board_moderation.v1"
NOTICE_BOARD_READS_SCHEMA = "tfi.portal.notice_board_reads.v1"
NOTICE_BOARD_CATEGORIES = (
    {"id": "bug", "label": "Bug"},
    {"id": "request", "label": "需求"},
    {"id": "release", "label": "Portal Release News"},
    {"id": "chat", "label": "闲谈"},
    {"id": "other", "label": "其他"},
)
_NOTICE_BOARD_CATEGORY_LABELS = {
    category["id"]: category["label"] for category in NOTICE_BOARD_CATEGORIES
}
DEFAULT_STALE_AFTER_DAYS = 30
MAX_POSTS = 500
MAX_REPLIES_PER_POST = 1000
MAX_TITLE_CHARS = 120
MAX_POST_CHARS = 30_000
MAX_REPLY_CHARS = 8_000
_POST_ID_RE = re.compile(r"notice_(?:[0-9a-f]{12}|[0-9a-f]{16})")
_REPLY_ID_RE = re.compile(r"reply_[0-9a-f]{16}")
_IMAGE_ID_RE = re.compile(r"notice_image_[0-9a-f]{16}\.(?:png|jpe?g|gif|webp)")
_MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9_@])@([A-Za-z0-9_+\-]+(?:\.[A-Za-z0-9_+\-]+)*)"
)
MAILBOX_KEY_PREFIX = "notice-board:"


def mailbox_key(post_id: object) -> str:
    """The Mailbox key of a post's mentions."""
    return f"{MAILBOX_KEY_PREFIX}{post_id}"


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


NOTICE_BOARD_STALE_AFTER_DAYS = _positive_int_env(
    "PORTAL_NOTICE_BOARD_STALE_DAYS",
    DEFAULT_STALE_AFTER_DAYS,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now_datetime() -> datetime:
    return datetime.now(timezone.utc)


class NoticeBoardBackend:
    def __init__(
        self,
        *,
        stale_after_days: int = NOTICE_BOARD_STALE_AFTER_DAYS,
    ) -> None:
        self.posts = NoticeState("posts")
        self.moderation = NoticeState("moderation")
        self.reads = NoticeState("reads")
        self.stale_after_days = max(1, int(stale_after_days))
        self._thread_lock = threading.RLock()

    def list_posts(self, actor: str) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            moderation = self._load_moderation()
            reads = self._load_reads(record)
        posts = []
        for post in record["posts"]:
            summary = self._post_summary(
                post,
                featured=self._featured_entry(moderation, post["post_id"]),
            )
            read_revision = self._read_revision(reads, clean_actor, post["post_id"])
            summary["unread"] = read_revision < post["notification_revision"]
            summary["unread_kind"] = (
                "post" if summary["unread"] and read_revision <= 0 else
                "reply" if summary["unread"] else ""
            )
            posts.append(summary)
        cutoff = _now_datetime() - timedelta(days=self.stale_after_days)
        for post in posts:
            post["folded"] = self._should_fold(post, cutoff=cutoff)
        posts.sort(key=self._post_sort_key, reverse=True)
        signature_source = json.dumps(
            posts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "posts": posts,
            "categories": [dict(category) for category in NOTICE_BOARD_CATEGORIES],
            "post_count": len(posts),
            "folded_count": sum(1 for post in posts if post["folded"]),
            "featured_count": sum(1 for post in posts if post["featured"]),
            "unread_count": sum(1 for post in posts if post["unread"]),
            "stale_after_days": self.stale_after_days,
            "content_signature": hashlib.sha256(signature_source).hexdigest(),
        }

    def get_post(self, actor: str, post_id: str) -> dict[str, Any]:
        self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        with self._thread_lock, self.posts.transaction():
            post = self._find_post(self._load_record(), clean_post_id)
            featured = self._featured_entry(
                self._load_moderation(),
                clean_post_id,
            )
        return {
            "post": self._public_post(
                post,
                featured=featured,
                actor=actor,
            ),
        }

    def classification_source(self, post_id: str) -> dict[str, str]:
        clean_post_id = self._clean_post_id(post_id)
        with self._thread_lock, self.posts.transaction():
            post = self._find_post(self._load_record(), clean_post_id)
        return {
            "post_id": clean_post_id,
            "title": post["title"],
            "content": post["content"],
            "category": post["category"],
            "fingerprint": self._classification_fingerprint(post),
        }

    def apply_automatic_category(
        self,
        post_id: str,
        *,
        expected_fingerprint: str,
        category: object,
    ) -> dict[str, Any]:
        clean_post_id = self._clean_post_id(post_id)
        clean_category = self._clean_category(category)
        changed = False
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            post = self._find_post(record, clean_post_id)
            if self._classification_fingerprint(post) != expected_fingerprint:
                return {"post_id": clean_post_id, "status": "stale", "changed": False}
            if post["category"] != clean_category:
                post["category"] = clean_category
                self._write_record(record)
                changed = True
        if changed:
            publish_local_projection_change(
                "notice-board",
                clean_post_id,
                fields=["category"],
                details={"source": "automatic-classifier"},
            )
        return {
            "post_id": clean_post_id,
            "status": "updated" if changed else "unchanged",
            "changed": changed,
            "category": clean_category,
        }

    def mention_events(self, actor: str) -> list[dict[str, Any]]:
        clean_actor = self._require_actor(actor)
        actor_key = clean_actor.casefold()
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
        events: list[dict[str, Any]] = []
        for post in record["posts"]:
            candidates: list[dict[str, Any]] = []
            if (
                actor_key in post["mentions"]
                and clean_actor.casefold() != str(post["author"]).casefold()
            ):
                occurred_at = str(
                    (post["mentions"].get(actor_key) or {}).get("mentioned_at")
                    or post["created_at"]
                )
                candidates.append({
                    "event_id": f"{post['post_id']}:post:{occurred_at}",
                    "post_id": post["post_id"],
                    "title": post["title"],
                    "author": post["author"],
                    "context": "post",
                    "occurred_at": occurred_at,
                })
            for reply in post["replies"]:
                if (
                    actor_key in self._mentions_in_text(reply["content"])
                    and clean_actor.casefold() != str(reply["author"]).casefold()
                ):
                    candidates.append({
                        "event_id": f"{post['post_id']}:reply:{reply['reply_id']}",
                        "post_id": post["post_id"],
                        "title": post["title"],
                        "author": reply["author"],
                        "context": "reply",
                        "occurred_at": reply["created_at"],
                    })
            if candidates:
                events.append(max(candidates, key=lambda item: self._time_sort_key(item["occurred_at"])))
        return sorted(events, key=lambda item: self._time_sort_key(item["occurred_at"]), reverse=True)

    def create_post(
        self,
        actor: str,
        title: str,
        content: str,
        category: object = "other",
    ) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        clean_title = self._clean_text(
            title,
            field="post title",
            max_chars=MAX_TITLE_CHARS,
            single_line=True,
        )
        clean_content = self._clean_text(
            content,
            field="post content",
            max_chars=MAX_POST_CHARS,
        )
        clean_category = self._clean_category(category)
        created_at = _now_iso()
        from management_common.resource_identity import get_resource_type_registry

        post = {
            "post_id": get_resource_type_registry().new_id("notice"),
            "title": clean_title,
            "content": clean_content,
            "category": clean_category,
            "author": clean_actor,
            "created_at": created_at,
            "updated_at": created_at,
            "edited_at": "",
            "notification_revision": 1,
            "mentions": self._mention_state(clean_content, created_at),
            "pinned": False,
            "pinned_at": "",
            "pinned_by": "",
            "replies": [],
        }
        with self._thread_lock, self.posts.transaction() as connection:
            record = self._load_record()
            reads = self._load_reads(record)
            if len(record["posts"]) >= MAX_POSTS:
                raise ValueError("notice board has reached its post limit")
            record["posts"].append(post)
            self._set_read_revision(
                reads,
                clean_actor,
                post["post_id"],
                post["notification_revision"],
                read_at=created_at,
            )
            self._write_record(record)
            self._write_reads(reads)
            self._sync_permissions(post["post_id"], author=clean_actor, connection=connection)
        mentioned = self._mentions_in_text(clean_content) - {clean_actor.casefold()}
        self._alert_mentions(post["post_id"], mentioned, f"post:{created_at}")
        self._publish_mentions_changed(post["post_id"], mentioned)
        return {
            "post": self._public_post(post, featured={}, actor=actor),
        }

    def update_post(
        self,
        actor: str,
        post_id: str,
        title: str,
        content: str,
        category: object | None = None,
    ) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        clean_title = self._clean_text(
            title,
            field="post title",
            max_chars=MAX_TITLE_CHARS,
            single_line=True,
        )
        clean_content = self._clean_text(
            content,
            field="post content",
            max_chars=MAX_POST_CHARS,
        )
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            moderation = self._load_moderation()
            post = self._find_post(record, clean_post_id)
            previous_mentions = self._mentions_in_text(post["content"])
            edited_at = _now_iso()
            post["title"] = clean_title
            post["content"] = clean_content
            if category is not None:
                post["category"] = self._clean_category(category)
            post["edited_at"] = edited_at
            post["updated_at"] = edited_at
            current_mentions = self._mentions_in_text(clean_content)
            post_mentions = dict(post.get("mentions") or {})
            for removed in previous_mentions - current_mentions:
                post_mentions.pop(removed, None)
            for added in current_mentions - previous_mentions:
                post_mentions[added] = {"mentioned_at": edited_at}
            post["mentions"] = post_mentions
            self._write_record(record)
        self._alert_mentions(
            clean_post_id, current_mentions - previous_mentions - {clean_actor.casefold()}, f"post:{edited_at}",
        )
        self._publish_mentions_changed(
            clean_post_id,
            (previous_mentions | current_mentions) - {clean_actor.casefold()},
        )
        return {
            "post": self._public_post(
                post,
                featured=self._featured_entry(moderation, clean_post_id),
                actor=actor,
            ),
        }

    def resolve_image(self, actor: str, image_id: str) -> tuple[bytes, str]:
        """The bytes and content type of an image pasted by the earlier board."""
        from management_portal.resources.registry_store import get_registry_store

        self._require_actor(actor)
        clean_image_id = str(image_id or "").strip()
        if not _IMAGE_ID_RE.fullmatch(clean_image_id):
            raise ValueError("invalid notice board image ID")
        document = get_registry_store().get(NOTICE_BOARD_IMAGE_COLLECTION, clean_image_id)
        if document is None:
            raise FileNotFoundError("notice board image not found")
        content_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }[Path(clean_image_id).suffix.casefold()]
        return base64.b64decode(str(document.get("data_base64") or "")), content_type

    def add_reply(self, actor: str, post_id: str, content: str) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        clean_content = self._clean_text(
            content,
            field="reply content",
            max_chars=MAX_REPLY_CHARS,
        )
        created_at = _now_iso()
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            moderation = self._load_moderation()
            reads = self._load_reads(record)
            post = self._find_post(record, clean_post_id)
            replies = post["replies"]
            if len(replies) >= MAX_REPLIES_PER_POST:
                raise ValueError("this post has reached its reply limit")
            replies.append(
                {
                    "reply_id": f"reply_{secrets.token_hex(8)}",
                    "author": clean_actor,
                    "content": clean_content,
                    "created_at": created_at,
                }
            )
            post["notification_revision"] += 1
            post["updated_at"] = created_at
            self._set_read_revision(
                reads,
                clean_actor,
                post["post_id"],
                post["notification_revision"],
                read_at=created_at,
            )
            self._write_record(record)
            self._write_reads(reads)
        mentioned = self._mentions_in_text(clean_content) - {clean_actor.casefold()}
        self._alert_mentions(clean_post_id, mentioned, f"reply:{replies[-1]['reply_id']}")
        self._publish_mentions_changed(clean_post_id, mentioned)
        return {
            "reply": dict(replies[-1]),
            "post": self._public_post(
                post,
                featured=self._featured_entry(moderation, clean_post_id),
                actor=actor,
            ),
        }

    def set_pinned(self, actor: str, post_id: str, pinned: bool) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        if not isinstance(pinned, bool):
            raise ValueError("pinned must be a boolean")
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            moderation = self._load_moderation()
            post = self._find_post(record, clean_post_id)
            post["pinned"] = pinned
            post["pinned_at"] = _now_iso() if pinned else ""
            post["pinned_by"] = clean_actor if pinned else ""
            self._write_record(record)
        return {
            "post": self._public_post(
                post,
                featured=self._featured_entry(moderation, clean_post_id),
                actor=actor,
            ),
        }

    def set_featured(
        self,
        actor: str,
        post_id: str,
        featured: bool,
    ) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        if not isinstance(featured, bool):
            raise ValueError("featured must be a boolean")
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            post = self._find_post(record, clean_post_id)
            moderation = self._load_moderation()
            featured_posts = moderation["featured_posts"]
            if featured:
                featured_posts[clean_post_id] = {
                    "featured_at": _now_iso(),
                    "featured_by": clean_actor,
                }
            else:
                featured_posts.pop(clean_post_id, None)
            self._write_moderation(moderation)
        return {
            "post": self._public_post(
                post,
                featured=self._featured_entry(moderation, clean_post_id),
                actor=actor,
            ),
        }

    def delete_post(self, actor: str, post_id: str) -> dict[str, Any]:
        self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        with self._thread_lock, self.posts.transaction() as connection:
            record = self._load_record()
            post = self._find_post(record, clean_post_id)
            mentioned_actors = self._mentioned_actors_in_post(post)
            record["posts"].remove(post)
            reads = self._load_reads(record)
            reads["baseline_revisions"].pop(clean_post_id, None)
            for user_state in reads["users"].values():
                user_state["posts"].pop(clean_post_id, None)
            self._write_record(record)
            self._write_reads(reads)
            moderation = self._load_moderation()
            if moderation["featured_posts"].pop(clean_post_id, None) is not None:
                self._write_moderation(moderation)
            self._sync_permissions(clean_post_id, deleted=True, connection=connection)
        self._publish_mentions_changed(clean_post_id, mentioned_actors)
        return {"deleted": True, "post_id": clean_post_id}

    def mark_post_read(
        self,
        actor: str,
        post_id: str,
        notification_revision: object,
    ) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        try:
            requested_revision = int(notification_revision)
        except (TypeError, ValueError) as exc:
            raise ValueError("notification revision must be an integer") from exc
        if requested_revision < 1:
            raise ValueError("notification revision must be positive")
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            post = self._find_post(record, clean_post_id)
            reads = self._load_reads(record)
            accepted_revision = min(
                requested_revision,
                int(post["notification_revision"]),
            )
            self._set_read_revision(
                reads,
                clean_actor,
                clean_post_id,
                accepted_revision,
            )
            self._write_reads(reads)
            current_revision = int(post["notification_revision"])
            saved_revision = self._read_revision(reads, clean_actor, clean_post_id)
        # Reading the whole post on Home reads its mentions in Mailbox too.
        if saved_revision >= current_revision and conversation_read_state.read_through(
            clean_actor, [mailbox_key(clean_post_id)],
        ):
            publish_local_projection_change(
                "agent-conversation-index", clean_post_id, fields=["notice-board-mentions"],
                details={"source": "notice-board"},
            )
        return {
            "post_id": clean_post_id,
            "read_revision": saved_revision,
            "notification_revision": current_revision,
            "unread": saved_revision < current_revision,
        }

    def mark_post_unread(self, actor: str, post_id: str) -> dict[str, Any]:
        """Move the read position back to just before the latest reply."""

        clean_actor = self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            post = self._find_post(record, clean_post_id)
            reads = self._load_reads(record)
            current_revision = int(post["notification_revision"])
            revision = max(0, current_revision - 1)
            user = reads["users"].setdefault(str(clean_actor).casefold(), {"actor": clean_actor, "posts": {}})
            user["actor"] = clean_actor
            user["posts"][clean_post_id] = {"revision": revision, "read_at": _now_iso()}
            self._write_reads(reads)
        return {
            "post_id": clean_post_id,
            "read_revision": revision,
            "notification_revision": current_revision,
            "unread": revision < current_revision,
        }

    def delete_reply(
        self,
        actor: str,
        post_id: str,
        reply_id: str,
    ) -> dict[str, Any]:
        clean_actor = self._require_actor(actor)
        clean_post_id = self._clean_post_id(post_id)
        clean_reply_id = self._clean_reply_id(reply_id)
        with self._thread_lock, self.posts.transaction():
            record = self._load_record()
            moderation = self._load_moderation()
            post = self._find_post(record, clean_post_id)
            reply = next(
                (
                    item
                    for item in post["replies"]
                    if item["reply_id"] == clean_reply_id
                ),
                None,
            )
            if reply is None:
                raise KeyError("notice board reply not found")
            from management_common.actor_identity import same_actor

            if not same_actor(reply["author"], clean_actor):
                raise PermissionError("只有回复作者本人可以删除回复")
            mentioned_actors = self._mentions_in_text(reply["content"])
            post["replies"].remove(reply)
            post["updated_at"] = self._latest_activity_at(post)
            self._write_record(record)
        self._publish_mentions_changed(clean_post_id, mentioned_actors)
        return {
            "post": self._public_post(
                post,
                featured=self._featured_entry(moderation, clean_post_id),
                actor=actor,
            ),
        }

    @staticmethod
    def _mentions_in_text(value: object) -> set[str]:
        return {
            match.group(1).casefold()
            for match in _MENTION_RE.finditer(str(value or ""))
        }

    @classmethod
    def _mention_state(cls, value: object, mentioned_at: str) -> dict[str, dict[str, str]]:
        return {
            actor: {"mentioned_at": mentioned_at}
            for actor in cls._mentions_in_text(value)
        }

    @classmethod
    def _mentioned_actors_in_post(cls, post: dict[str, Any]) -> set[str]:
        actors = cls._mentions_in_text(post.get("content"))
        for reply in post.get("replies") or []:
            if isinstance(reply, dict):
                actors.update(cls._mentions_in_text(reply.get("content")))
        return actors

    @staticmethod
    def _alert_mentions(post_id: str, actors: set[str], event: str) -> None:
        """The mentioned users' Mailbox rows are created with the mention."""
        if actors:
            conversation_read_state.alert(actors, mailbox_key(post_id), event)

    @staticmethod
    def _publish_mentions_changed(post_id: str, actors: set[str]) -> None:
        if not actors:
            return
        publish_local_projection_change(
            "agent-conversation-index",
            post_id,
            fields=["notice-board-mentions"],
            details={"source": "notice-board"},
        )

    def _load_record(self) -> dict[str, Any]:
        payload = self.posts.read({"schema": NOTICE_BOARD_SCHEMA, "posts": []})
        if not isinstance(payload, dict):
            raise RuntimeError("notice board storage must contain a JSON object")
        posts = payload.get("posts")
        if not isinstance(posts, list):
            raise RuntimeError("notice board storage has an invalid posts list")
        return {
            "schema": NOTICE_BOARD_SCHEMA,
            "posts": [self._normalize_post(post) for post in posts if isinstance(post, dict)],
        }

    def _load_moderation(self) -> dict[str, Any]:
        payload = self.moderation.read({"schema": NOTICE_BOARD_MODERATION_SCHEMA, "featured_posts": {}})
        if not isinstance(payload, dict):
            raise RuntimeError(
                "notice board moderation storage must contain a JSON object"
            )
        raw_featured = payload.get("featured_posts")
        if not isinstance(raw_featured, dict):
            raise RuntimeError(
                "notice board moderation storage has an invalid featured_posts map"
            )
        return {
            "schema": NOTICE_BOARD_MODERATION_SCHEMA,
            "featured_posts": {
                str(post_id): {
                    "featured_at": str(entry.get("featured_at") or ""),
                    "featured_by": str(entry.get("featured_by") or ""),
                }
                for post_id, entry in raw_featured.items()
                if _POST_ID_RE.fullmatch(str(post_id))
                and isinstance(entry, dict)
            },
        }

    def _load_reads(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = self.reads.read()
        if payload is None:
            reads = {
                "schema": NOTICE_BOARD_READS_SCHEMA,
                "baseline_revisions": {
                    post["post_id"]: int(post["notification_revision"])
                    for post in record.get("posts") or []
                    if isinstance(post, dict) and _POST_ID_RE.fullmatch(post["post_id"])
                },
                "users": {},
            }
            self._write_reads(reads)
            return reads
        if not isinstance(payload, dict):
            raise RuntimeError("notice board read-state storage must contain an object")
        raw_baseline = payload.get("baseline_revisions")
        raw_users = payload.get("users")
        if not isinstance(raw_baseline, dict) or not isinstance(raw_users, dict):
            raise RuntimeError("notice board read-state storage has an invalid shape")
        baseline: dict[str, int] = {}
        for post_id, revision in raw_baseline.items():
            try:
                normalized_revision = max(0, int(revision))
            except (TypeError, ValueError):
                continue
            if _POST_ID_RE.fullmatch(str(post_id)):
                baseline[str(post_id)] = normalized_revision
        users: dict[str, dict[str, Any]] = {}
        for actor_key, raw_user in raw_users.items():
            if not isinstance(raw_user, dict):
                continue
            raw_posts = raw_user.get("posts")
            if not isinstance(raw_posts, dict):
                continue
            posts: dict[str, dict[str, Any]] = {}
            for post_id, raw_entry in raw_posts.items():
                if not _POST_ID_RE.fullmatch(str(post_id)):
                    continue
                if isinstance(raw_entry, dict):
                    revision = raw_entry.get("revision")
                    read_at = str(raw_entry.get("read_at") or "")
                else:
                    revision = raw_entry
                    read_at = ""
                try:
                    normalized_revision = max(0, int(revision))
                except (TypeError, ValueError):
                    continue
                posts[str(post_id)] = {
                    "revision": normalized_revision,
                    "read_at": read_at,
                }
            clean_actor_key = str(actor_key or "").strip().casefold()
            if clean_actor_key:
                users[clean_actor_key] = {
                    "actor": str(raw_user.get("actor") or actor_key),
                    "posts": posts,
                }
        return {
            "schema": NOTICE_BOARD_READS_SCHEMA,
            "baseline_revisions": baseline,
            "users": users,
        }

    def _write_record(self, record: dict[str, Any]) -> None:
        self.posts.replace(record)

    def _write_moderation(self, moderation: dict[str, Any]) -> None:
        self.moderation.replace(moderation)

    def _write_reads(self, reads: dict[str, Any]) -> None:
        self.reads.replace(reads)

    @staticmethod
    def _read_revision(
        reads: dict[str, Any],
        actor: str,
        post_id: str,
    ) -> int:
        baseline = int(reads["baseline_revisions"].get(post_id) or 0)
        user = reads["users"].get(str(actor or "").casefold()) or {}
        entry = (user.get("posts") or {}).get(post_id)
        return int(entry.get("revision") or 0) if entry is not None else baseline

    @classmethod
    def _set_read_revision(
        cls,
        reads: dict[str, Any],
        actor: str,
        post_id: str,
        revision: int,
        *,
        read_at: str | None = None,
    ) -> None:
        actor_key = str(actor or "").casefold()
        user = reads["users"].setdefault(
            actor_key,
            {"actor": actor, "posts": {}},
        )
        user["actor"] = actor
        accepted_revision = max(
            cls._read_revision(reads, actor, post_id),
            int(revision),
        )
        user["posts"][post_id] = {
            "revision": accepted_revision,
            "read_at": str(read_at or _now_iso()),
        }


    @staticmethod
    def _normalize_post(post: dict[str, Any]) -> dict[str, Any]:
        replies = post.get("replies")
        if not isinstance(replies, list):
            replies = []
        created_at = str(post.get("created_at") or "")
        edited_at = str(post.get("edited_at") or "")
        raw_mentions = post.get("mentions")
        if isinstance(raw_mentions, dict):
            mentions = {
                str(actor).casefold(): {
                    "mentioned_at": str(
                        value.get("mentioned_at")
                        if isinstance(value, dict)
                        else ""
                    ) or edited_at or created_at,
                }
                for actor, value in raw_mentions.items()
                if str(actor).strip()
            }
        else:
            mentions = NoticeBoardBackend._mention_state(
                post.get("content"),
                edited_at or created_at,
            )
        return {
            "post_id": str(post.get("post_id") or ""),
            "title": str(post.get("title") or ""),
            "content": str(post.get("content") or ""),
            "category": NoticeBoardBackend._category_or_other(post.get("category")),
            "author": str(post.get("author") or "anonymous"),
            "created_at": created_at,
            "updated_at": str(post.get("updated_at") or post.get("created_at") or ""),
            "edited_at": edited_at,
            "notification_revision": max(
                1,
                int(post.get("notification_revision") or (len(replies) + 1)),
            ),
            "mentions": mentions,
            "pinned": bool(post.get("pinned")),
            "pinned_at": str(post.get("pinned_at") or ""),
            "pinned_by": str(post.get("pinned_by") or ""),
            "replies": [
                {
                    "reply_id": str(reply.get("reply_id") or ""),
                    "author": str(reply.get("author") or "anonymous"),
                    "content": str(reply.get("content") or ""),
                    "created_at": str(reply.get("created_at") or ""),
                }
                for reply in replies
                if isinstance(reply, dict)
            ],
        }

    @staticmethod
    def _post_summary(
        post: dict[str, Any],
        *,
        featured: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "post_id": post["post_id"],
            "title": post["title"],
            "category": post["category"],
            "category_label": _NOTICE_BOARD_CATEGORY_LABELS[post["category"]],
            "author": post["author"],
            "created_at": post["created_at"],
            "updated_at": post["updated_at"],
            "edited_at": post["edited_at"],
            "notification_revision": post["notification_revision"],
            "reply_count": len(post["replies"]),
            "pinned": post["pinned"],
            "pinned_at": post["pinned_at"],
            "pinned_by": post["pinned_by"],
            "featured": bool(featured),
            "featured_at": str(featured.get("featured_at") or ""),
            "featured_by": str(featured.get("featured_by") or ""),
        }

    @classmethod
    def _public_post(
        cls,
        post: dict[str, Any],
        *,
        featured: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        """The full post; post mutations follow the permission scheme, and replies belong to their authors."""
        from management_common.actor_identity import same_actor

        result = cls._post_summary(post, featured=featured)
        result["content"] = post["content"]
        result["can_edit"] = True
        result["can_delete"] = True
        result["replies"] = [
            {
                **reply,
                "floor": index,
                "can_delete": same_actor(reply["author"], actor),
            }
            for index, reply in enumerate(post["replies"], start=1)
        ]
        return result

    @classmethod
    def _latest_activity_at(cls, post: dict[str, Any]) -> str:
        timestamps = [
            str(post.get("created_at") or ""),
            str(post.get("edited_at") or ""),
        ]
        timestamps.extend(
            str(reply.get("created_at") or "")
            for reply in post.get("replies") or []
            if isinstance(reply, dict)
        )
        return max(timestamps, key=cls._time_sort_key)

    @staticmethod
    def _sync_permissions(post_id, *, author="", deleted=False, connection):
        """Place a new post (its author is its creator) in the permission instance, or take a deleted one out, inside the post's transaction."""
        from management_portal import permission_system

        if deleted:
            return permission_system.retire_resources([post_id], connection=connection)
        return permission_system.sync_resource({"resource_id": post_id, "author": author},
                                               resource_type="notice", connection=connection)

    @classmethod
    def _post_sort_key(cls, post: dict[str, Any]) -> tuple[int, datetime]:
        priority = 2 if post.get("pinned") else 1 if post.get("featured") else 0
        return (priority, cls._time_sort_key(post.get("updated_at")))

    @classmethod
    def _time_sort_key(cls, value: object) -> datetime:
        return cls._parse_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)

    @staticmethod
    def _classification_fingerprint(post: dict[str, Any]) -> str:
        source = json.dumps(
            {
                "title": str(post.get("title") or ""),
                "content": str(post.get("content") or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(source).hexdigest()

    @staticmethod
    def _featured_entry(
        moderation: dict[str, Any],
        post_id: str,
    ) -> dict[str, Any]:
        entry = (moderation.get("featured_posts") or {}).get(post_id)
        return dict(entry) if isinstance(entry, dict) else {}

    @classmethod
    def _should_fold(
        cls,
        post: dict[str, Any],
        *,
        cutoff: datetime,
    ) -> bool:
        if post.get("pinned") or post.get("featured"):
            return False
        activity_at = cls._parse_datetime(
            post.get("updated_at") or post.get("created_at")
        )
        return bool(activity_at and activity_at < cutoff)

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _find_post(record: dict[str, Any], post_id: str) -> dict[str, Any]:
        for post in record["posts"]:
            if post["post_id"] == post_id:
                return post
        raise KeyError("notice board post not found")

    @staticmethod
    def _require_actor(actor: str) -> str:
        clean_actor = str(actor or "").strip()
        if not clean_actor:
            raise PermissionError("login required")
        return clean_actor

    @staticmethod
    def _clean_post_id(post_id: str) -> str:
        clean_post_id = str(post_id or "").strip()
        if not _POST_ID_RE.fullmatch(clean_post_id):
            raise ValueError("invalid notice board post ID")
        return clean_post_id

    @staticmethod
    def _clean_reply_id(reply_id: str) -> str:
        clean_reply_id = str(reply_id or "").strip()
        if not _REPLY_ID_RE.fullmatch(clean_reply_id):
            raise ValueError("invalid notice board reply ID")
        return clean_reply_id

    @staticmethod
    def _clean_category(category: object) -> str:
        clean_category = str(category or "").strip().casefold()
        if clean_category not in _NOTICE_BOARD_CATEGORY_LABELS:
            raise ValueError("invalid notice board category")
        return clean_category

    @staticmethod
    def _category_or_other(category: object) -> str:
        clean_category = str(category or "").strip().casefold()
        return clean_category if clean_category in _NOTICE_BOARD_CATEGORY_LABELS else "other"

    @staticmethod
    def _clean_text(
        value: str,
        *,
        field: str,
        max_chars: int,
        single_line: bool = False,
    ) -> str:
        text = str(value or "").strip()
        if single_line:
            text = " ".join(text.split())
        if not text:
            raise ValueError(f"{field} required")
        if len(text) > max_chars:
            raise ValueError(f"{field} exceeds {max_chars} characters")
        return text


notice_board = NoticeBoardBackend()


def resource_records() -> list[dict[str, Any]]:
    """Every post as the permission system places it (``permission.records`` hook): its author created it."""
    return [{"resource_id": post["post_id"], "resource_type": "notice", "created_by": post.get("author"),
             "created_for": post.get("created_for")}
            for post in notice_board.posts.read().get("posts") or [] if str(post.get("post_id") or "").strip()]


def mention_entries(events: object) -> list[dict[str, Any]]:
    """Mention events as Mailbox notifications, one per post."""
    from management_portal.agent_conversation_index_backend import normalized_entries

    items = events if isinstance(events, list) else []
    entries: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        post_id = str(raw.get("post_id") or "").strip()
        event_id = str(raw.get("event_id") or "").strip()
        occurred_at = str(raw.get("occurred_at") or "").strip()
        author = str(raw.get("author") or "").strip()
        if not post_id or not event_id or not occurred_at:
            continue
        context = str(raw.get("context") or "post").strip()
        entries.append({
            "key": mailbox_key(post_id),
            "title": str(raw.get("title") or "Notice Board").strip(),
            "subtitle": f"{author or '用户'} 在{'回复' if context == 'reply' else '帖子'}中提到了你",
            "surface": "home",
            "surface_label": "Notice Board",
            "kind": "notice-board-mention",
            "url": f"/?notice_post={post_id}",
            "updated_at": occurred_at,
            "status": "idle",
        })
    return normalized_entries(entries)


def mailbox_entries(actor: str) -> list[dict[str, Any]]:
    return mention_entries(notice_board.mention_events(actor))


# Posts and replies that mention a User are notifications in that User's Mailbox.
MAILBOX_SOURCE = MailboxSource(MAILBOX_KEY_PREFIX, mailbox_entries)
