"""Authenticated, opaque-origin previews of RunScript artifacts.

Bytes come exclusively from ISA reads under the viewer's identity. Do not
pass these documents through Portal's HTML bootstrap/asset injection.
"""
from __future__ import annotations

import base64
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote

from . import isa_client

from management_portal.http_routes import RouteDefinition


MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
ARTIFACT_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; "
    "font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)


class ScriptRunHttp:
    def __init__(self, *, respond, exception, payload):
        self.respond = respond
        self.exception = exception
        self.payload = payload

    def artifact(self, request, parsed, actor):
        query = parse_qs(parsed.query)
        run_id = (query.get("script_run_id") or [""])[0]
        lineage_id = (query.get("lineage_id") or [""])[0]
        name = (query.get("name") or [""])[0]
        if not lineage_id or not run_id or not name:
            return self.respond(request, 400, {"error": "请指定脚本运行和产物名称"})
        try:
            run = isa_client.call(actor, "GetScriptRun", {"lineage_id": lineage_id, "script_run_id": run_id})
            artifact = next((item for item in run.get("artifacts") or [] if item.get("name") == name), None)
            if artifact is None:
                return self.respond(request, 404, {"error": "产物不存在"})
            size = int(artifact["size"])
            if size < 0 or size > MAX_ARTIFACT_BYTES:
                raise ValueError("产物超过读取上限")
            data = bytearray()
            while True:
                chunk = isa_client.call(actor, "ReadScriptRunOutput", {
                    "lineage_id": lineage_id, "script_run_id": run_id, "stream": "artifact", "name": name,
                    "offset": len(data), "limit": 65536,
                })
                raw = base64.b64decode(chunk["base64"], validate=True)
                if int(chunk["offset"]) != len(data) or int(chunk["next_offset"]) != len(data) + len(raw):
                    raise ValueError("产物读取偏移不一致")
                data.extend(raw)
                if len(data) > size or int(chunk["size"]) != size:
                    raise ValueError("产物读取期间大小发生变化")
                if chunk["eof"]:
                    if len(data) != size:
                        raise ValueError("产物读取不完整")
                    break
                if not raw:
                    raise ValueError("产物读取没有进展")
            media = str(artifact.get("mime_type") or "application/octet-stream").split(";", 1)[0].lower()
            suffix = PurePosixPath(name).suffix.lower()
            if suffix in {".html", ".htm"}:
                media = "text/html"
            # Active formats other than HTML never get an inline document origin.
            inline = media == "text/html" or media.startswith("text/") or media in {
                "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
            }
            if media.startswith("text/") and media != "text/html":
                media = "text/plain"
            if not inline:
                media = "application/octet-stream"
            disposition = "attachment" if (query.get("download") or [""])[0] == "1" or not inline else "inline"
            headers = {
                "Content-Security-Policy": ARTIFACT_CSP,
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(PurePosixPath(name).name, safe='')}",
            }
            if media.startswith("text/"):
                media += "; charset=utf-8"
            return self.payload(request, 200, bytes(data), media,
                                cache_control="no-store", extra_headers=headers)
        except Exception as exc:
            return self.exception(request, exc)

    def routes(self):
        """The script run artifacts."""
        return (
            RouteDefinition("GET", "/api/script-runs/artifact", self.artifact, "portal-script-runs"),
        )
