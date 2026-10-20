from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .resource_refs import resolve_resource_references

AGENT_URL = os.getenv("KNOWLEDGE_ARCHIVE_AGENT_URL", "http://127.0.0.1:8767").rstrip("/")


class KnowledgeArchiveServiceUnavailable(RuntimeError):
    pass


class KnowledgeArchiveBackendClient:
    def __init__(self, base_url: str = AGENT_URL):
        self.base_url = base_url.rstrip("/")

    def list_jobs(self, actor: str, limit: int = 20) -> dict[str, Any]:
        query = urllib.parse.urlencode({"actor": actor or "anonymous", "limit": int(limit or 20)})
        return self._request("GET", f"/api/archive/jobs?{query}")

    def get_job(self, actor: str, archive_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"actor": actor or "anonymous", "id": archive_id})
        return self._request("GET", f"/api/archive/job?{query}")

    def create_job(self, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload or {})
        body["actor"] = actor or "anonymous"
        return self._request("POST", "/api/archive/create", body)

    def send_message(
        self,
        actor: str,
        archive_id: str,
        content: str,
        *,
        message_id: str,
        input_media: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "actor": actor or "anonymous",
            "archive_id": archive_id,
            "content": content,
            "message_id": str(message_id or "").strip(),
            "input_media": list(input_media or []),
        }
        resources = resolve_resource_references(
            content=content,
            actor=actor,
        )
        if resources:
            payload["context_refs"] = {"resources": resources}
        return self._request("POST", "/api/archive/chat", payload)

    def update_candidates(self, actor: str, archive_id: str, candidates: list[Any]) -> dict[str, Any]:
        return self._request("POST", "/api/archive/candidates", {
            "actor": actor or "anonymous",
            "archive_id": archive_id,
            "candidates": candidates,
        })

    def generate_drafts(self, actor: str, archive_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/archive/drafts", {
            "actor": actor or "anonymous",
            "archive_id": archive_id,
        })

    def apply_drafts(self, actor: str, archive_id: str, draft_ids: list[Any]) -> dict[str, Any]:
        return self._request("POST", "/api/archive/apply", {
            "actor": actor or "anonymous",
            "archive_id": archive_id,
            "draft_ids": draft_ids,
        })

    def update_draft_content(self, actor: str, archive_id: str, draft_id: str, content: str) -> dict[str, Any]:
        return self._request("POST", "/api/archive/draft/save", {
            "actor": actor or "anonymous",
            "archive_id": archive_id,
            "draft_id": draft_id,
            "content": content,
        })

    def delete_job(self, actor: str, archive_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/archive/delete", {
            "actor": actor or "anonymous",
            "archive_id": archive_id,
        })

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=540) as resp:
                return self._decode_response(resp.read())
        except urllib.error.HTTPError as exc:
            error = self._decode_response(exc.read()).get("error") or str(exc)
            if exc.code == 503:
                raise KnowledgeArchiveServiceUnavailable(error)
            if exc.code == 400:
                raise ValueError(error)
            if exc.code == 403:
                raise PermissionError(error)
            if exc.code == 404:
                raise KeyError(error)
            raise RuntimeError(error)
        except urllib.error.URLError as exc:
            raise KnowledgeArchiveServiceUnavailable("KnowledgeArchiveAgent 未运行，归档功能暂不可用。") from exc

    def _decode_response(self, raw: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("invalid KnowledgeArchiveAgent response") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("invalid KnowledgeArchiveAgent payload")
        return payload


knowledge_archive_backend = KnowledgeArchiveBackendClient()
