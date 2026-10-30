"""Article build and workspace HTTP routes."""

import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote

from management_portal import (
    article_build, article_build_profile, article_build_results, article_files, article_git, article_registry,
    article_semantic_index, article_settings, article_synctex, article_texlab_backend, http_auth,
    http_documents, http_files, http_request_body, http_responses, web_resources,
)
from management_portal.http_routes import ROUTE_NOT_FOUND, RouteDefinition
from management_portal.resources import get_resource_service

ROOT = Path(__file__).resolve().parent


class ArticlesHttp:
    def __init__(self, *, respond, exception_response, read_json, read_multipart,
                 multipart_limit, body_too_large, session_actor, require_resource,
                 notify_index, texlab_backend,
                 resource_service, serve_media, serve_download, media_content_type):
        self.respond = respond
        self.exception_response = exception_response
        self.read_json = read_json
        self.read_multipart = read_multipart
        self.multipart_limit = multipart_limit
        self.body_too_large = body_too_large
        self.session_actor = session_actor
        self.require_resource = require_resource
        self.notify_index = notify_index
        self.texlab_backend = texlab_backend
        self.resource_service = resource_service
        self.serve_media = serve_media
        self.serve_download = serve_download
        self.media_content_type = media_content_type

    def with_action(self, operation, action):
        """Bind a literal route action while preserving template parameters."""
        def route(request, parsed, actor, params):
            return operation(request, parsed, actor, {**params, "action": action})
        return route

    def list_articles(self, request, parsed, actor):
        articles = []
        user = self.session_actor(request) or ""
        try:
            visible_ids = {
                str(item.get("resource_id") or "")
                for item in self.resource_service().query(
                    resource_types=["article"], actor=user, limit=5000,
                ).get("resources") or []
            }
            for record in article_registry.load_article_records():
                if str(record.get("id") or "") not in visible_ids:
                    continue
                item = dict(record)
                try:
                    pdf_path = article_build_results.compiled_article_pdf(str(record.get("id") or ""))
                    item["compiled_pdf"] = {
                        "available": True,
                        "size": pdf_path.stat().st_size,
                        "modified_at": datetime.fromtimestamp(pdf_path.stat().st_mtime).astimezone().isoformat(),
                        "url": f"/api/articles/{quote(str(record.get('id') or ''), safe='')}/pdf",
                    }
                except FileNotFoundError:
                    item["compiled_pdf"] = {"available": False}
                articles.append(item)
            return self.respond(request, 200, {"articles": articles})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def workspace_read(self, request, parsed, actor, params):
        article_id, action = params["article_id"], params["action"]
        if not re.fullmatch(r"article_[0-9a-f]{12}", article_id) or action not in {
            "files", "file", "raw", "search", "settings", "build", "semantic", "preflight",
        }:
            return ROUTE_NOT_FOUND
        qs = parse_qs(parsed.query)
        try:
            self.require_resource(article_id, self.session_actor(request) or "")
            if action == "files":
                result = article_files.list_article_files(article_id)
            elif action in {"file", "raw"}:
                relative_path = str((qs.get("path") or [""])[0]).strip()
                if not relative_path:
                    raise ValueError("article file path required")
                if action == "raw":
                    file_path = article_files.resolve_article_raw_file(article_id, relative_path)
                    content_type = self.media_content_type(file_path)
                    if (qs.get("download") or [""])[0] == "1" or not content_type:
                        return self.serve_download(request, file_path)
                    return self.serve_media(request, file_path, content_type)
                reader = article_files.describe_article_file if (qs.get("meta") or [""])[0] == "1" else article_files.read_article_file
                result = reader(article_id, relative_path)
            elif action == "search":
                result = article_files.search_article_files(article_id, str((qs.get("q") or [""])[0]))
            elif action == "settings":
                result = article_settings.load_article_settings(article_id)
            elif action == "preflight":
                result = article_build_profile.article_build_preflight(article_id)
            elif action == "semantic":
                result = article_semantic_index.article_semantic_index(article_id)
            else:
                result = article_build_results.load_article_build_report(article_id)
                if result.get("pdf_available"):
                    result["pdf_url"] = f"/api/articles/{article_id}/pdf"
            return self.respond(request, 200, result)
        except KeyError:
            return self.respond(request, 404, {"error": "article not found"})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def git_read(self, request, parsed, actor, params):
        article_id = params["article_id"]
        action = params.get("action", "")
        if not re.fullmatch(r"article_[0-9a-f]{12}", article_id) or action not in {"", "diff", "history"}:
            return ROUTE_NOT_FOUND
        qs = parse_qs(parsed.query)
        try:
            self.require_resource(article_id, self.session_actor(request) or "")
            if action == "diff":
                commit_sha = str((qs.get("commit") or [""])[0]).strip()
                result = article_git.article_git_commit_diff(article_id, commit_sha) if commit_sha else article_git.article_git_diff(article_id, str((qs.get("path") or [""])[0]))
            elif action == "history":
                result = article_git.article_git_history(article_id, limit=int((qs.get("limit") or [20])[0]))
            else:
                result = article_git.article_git_status(article_id)
            return self.respond(request, 200, result)
        except KeyError:
            return self.respond(request, 404, {"error": "article not found"})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def pdf_read(self, request, parsed, actor, params):
        article_id = params["article_id"]
        if not re.fullmatch(r"article_[0-9a-f]{12}", article_id):
            return ROUTE_NOT_FOUND
        try:
            self.require_resource(article_id, self.session_actor(request) or "")
            path = article_build_results.compiled_article_pdf(article_id)
            return self.serve_media(request, path, "application/pdf")
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except FileNotFoundError:
            return self.respond(request, 404, {"error": "compiled article PDF not found"})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})

    def preflight(self, request, parsed, actor):
        payload = self.read_json(request)
        try:
            result = article_build_profile.inspect_article_build_profile(
                str(payload.get("path") or ""),
                primary_file=str(payload.get("primary_file") or ""),
                compiler=str(payload.get("compiler") or ""),
                require_toolchain=False,
            )
            if not result.get("project_ids"):
                raise ValueError("Article 目录必须位于已登记的 Project 内")
            return self.respond(request, 200, result)
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def language(self, request, parsed, actor, params):
        article_id = params["article_id"]
        action = params["action"]
        if not re.fullmatch(r"article_[0-9a-f]{12}", article_id) or action not in {
            "texlab", "source-to-pdf", "pdf-to-source",
        }:
            return ROUTE_NOT_FOUND
        payload = self.read_json(request)
        try:
            self.require_resource(article_id, self.session_actor(request) or "")
            if action == "texlab":
                result = self.texlab_backend().article_texlab_request(article_id, payload)
            elif action == "source-to-pdf":
                result = article_synctex.article_source_to_pdf(
                    article_id,
                    path=str(payload.get("path") or ""),
                    line=int(payload.get("line") or 1),
                    column=int(payload.get("column") or 1),
                    page_hint=int(payload.get("page_hint") or 0),
                )
            else:
                result = article_synctex.article_pdf_to_source(
                    article_id,
                    page=int(payload.get("page") or 1),
                    x=float(payload.get("x") or 0),
                    y=float(payload.get("y") or 0),
                )
            return self.respond(request, 200, result)
        except KeyError:
            return self.respond(request, 404, {"error": "article not found"})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except LookupError as exc:
            return self.respond(request, 422, {"error": str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except (ValueError, TimeoutError) as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except RuntimeError as exc:
            return self.respond(request, 503, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def workspace(self, request, parsed, actor, params):
        article_id = params["article_id"]
        action = params["action"]
        if not re.fullmatch(r"article_[0-9a-f]{12}", article_id):
            return ROUTE_NOT_FOUND
        try:
            self.require_resource(article_id, self.session_actor(request) or "")
            if (
                action == "upload"
                and str(request.headers.get("Content-Type") or "")
                .casefold().startswith("multipart/form-data")
            ):
                payload = self.read_multipart(
                    request,
                    max_bytes=self.multipart_limit(article_files.ARTICLE_UPLOAD_MAX_BYTES),
                )
            else:
                payload = self.read_json(request)
            if action == "file":
                result = article_files.write_article_file(
                    article_id, str(payload.get("path") or "").strip(),
                    str(payload.get("content") or ""),
                )
            elif action == "entries":
                result = article_files.create_article_entry(
                    article_id, str(payload.get("path") or "").strip(),
                    str(payload.get("kind") or "").strip(),
                )
            elif action == "upload":
                result = article_files.upload_article_file(
                    article_id,
                    filename=str(payload.get("filename") or ""),
                    data=bytes(payload.get("data_bytes") or b""),
                    directory=str(payload.get("directory") or "").strip(),
                )
            else:
                result = article_settings.update_article_settings(article_id, payload)
            self.notify_index(resource_id=article_id, resource_type="article", kind="updated")
            return self.respond(request, 201 if action in {"entries", "upload"} else 200, result)
        except KeyError:
            return self.respond(request, 404, {"error": "article not found"})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except FileExistsError as exc:
            return self.respond(request, 409, {"error": f"already exists: {exc}"})
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except self.body_too_large as exc:
            return self.respond(request, 413, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def git(self, request, parsed, actor, params):
        article_id = params["article_id"]
        action = params["action"]
        if not re.fullmatch(r"article_[0-9a-f]{12}", article_id) or action not in {"init", "stage", "commit"}:
            return ROUTE_NOT_FOUND
        payload = self.read_json(request)
        actor = self.session_actor(request) or ""
        try:
            self.require_resource(article_id, actor)
            if action == "init":
                result = article_git.initialize_article_git(article_id)
            elif action == "stage":
                raw_paths = payload.get("paths") or []
                if not isinstance(raw_paths, list):
                    raise ValueError("paths must be a list")
                result = article_git.stage_article_git_paths(
                    article_id, [str(path) for path in raw_paths],
                    staged=bool(payload.get("staged", True)),
                )
            else:
                result = article_git.commit_article_git_changes(
                    article_id, str(payload.get("message") or ""),
                    description=str(payload.get("description") or ""), actor=actor,
                )
            return self.respond(request, 200, result)
        except KeyError:
            return self.respond(request, 404, {"error": "article not found"})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 409, {"error": str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def action(self, request, parsed, actor, params):
        article_id = params["article_id"]
        action = params["action"]
        if not re.fullmatch(r"article_[0-9a-f]{12}", article_id):
            return ROUTE_NOT_FOUND
        try:
            self.require_resource(article_id, self.session_actor(request) or "")
            if action == "stop":
                return self.respond(request, 200, article_build.stop_article_compile(article_id))
            payload = self.read_json(request)
            from_scratch = payload.get("from_scratch", False)
            if not isinstance(from_scratch, bool):
                raise ValueError("from_scratch must be a boolean")
            result = article_build.compile_article_pdf(article_id, from_scratch=from_scratch)
            result["pdf_url"] = f"/api/articles/{article_id}/pdf?v={int(time.time())}"
            return self.respond(request, 200, result)
        except KeyError:
            return self.respond(request, 404, {"error": "article not found"})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {"error": str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except RuntimeError as exc:
            message, _separator, details = str(exc).partition("\n")
            return self.respond(request, 422, {
                "error": message or "LaTeX compilation failed",
                "details": details[-12000:],
            })
        except Exception as exc:
            return self.exception_response(request, exc)


def page_assets() -> tuple[str, ...]:
    """The Article page's own style sheet and scripts: the parts and the root script it loads."""
    page = (ROOT / "article_workspace.html").read_text(encoding="utf-8")
    return tuple(dict.fromkeys(re.findall(r'(?:src|href)="(/static/article_workspace[a-z_]*\.(?:css|js))"', page)))


def routes(portal):
    """The Article workspace page, list and API (``web.routes``); ``portal`` is the Web shell."""
    http = ArticlesHttp(
        respond=lambda *args: http_responses._serve_json(*args),
        exception_response=lambda request, exc: http_responses._serve_exception_json(request, exc),
        read_json=lambda request: http_request_body._read_request_json(request),
        read_multipart=lambda request, **kwargs: http_request_body._read_request_multipart(request, **kwargs),
        multipart_limit=lambda size: http_request_body._multipart_body_limit(size),
        body_too_large=http_request_body.RequestBodyTooLarge,
        session_actor=lambda request: http_auth._get_session_username(request),
        require_resource=lambda *args: web_resources._require_resource(*args),
        notify_index=lambda **kwargs: web_resources._notify_resource_index(**kwargs),
        texlab_backend=lambda: article_texlab_backend,
        resource_service=lambda: get_resource_service(),
        serve_media=lambda *args: http_files._serve_media_file(*args),
        serve_download=lambda *args: http_files._serve_download_file(*args),
        media_content_type=lambda path: http_files._media_content_type(path),
    )

    def page(request, parsed, actor):
        return http_documents._serve_authenticated_document(request, 200, (ROOT / "article_workspace.html").read_bytes())

    read, write, git = http.workspace_read, http.workspace, http.git_read
    templates = (
        ("GET", "files", http.with_action(read, "files")),
        ("GET", "file", http.with_action(read, "file")),
        ("GET", "raw", http.with_action(read, "raw")),
        ("GET", "search", http.with_action(read, "search")),
        ("GET", "settings", http.with_action(read, "settings")),
        ("GET", "build", http.with_action(read, "build")),
        ("GET", "semantic", http.with_action(read, "semantic")),
        ("GET", "preflight", http.with_action(read, "preflight")),
        ("GET", "git", git),
        ("GET", "git/diff", http.with_action(git, "diff")),
        ("GET", "git/history", http.with_action(git, "history")),
        ("GET", "pdf", http.pdf_read),
        ("POST", "texlab", http.with_action(http.language, "texlab")),
        ("POST", "synctex/{action}", http.language),
        ("POST", "file", http.with_action(write, "file")),
        ("POST", "entries", http.with_action(write, "entries")),
        ("POST", "upload", http.with_action(write, "upload")),
        ("POST", "settings", http.with_action(write, "settings")),
        ("POST", "git/{action}", http.git),
        ("POST", "compile", http.with_action(http.action, "compile")),
        ("POST", "stop", http.with_action(http.action, "stop")),
    )
    return (
        *(RouteDefinition("GET", path, portal._PUBLIC_PAGES_HTTP.static, "portal-public-get", phase="public")
          for path in page_assets()),
        RouteDefinition("GET", "/api/articles", http.list_articles, "portal-catalog-get"),
        RouteDefinition("POST", "/api/articles/preflight", http.preflight, "portal-articles"),
        *(RouteDefinition(method, f"/api/articles/{{article_id}}/{path}", handler, "portal-articles", match="template")
          for method, path, handler in templates),
        *(RouteDefinition("GET", path, page, "portal-pages-get", page=True)
          for path in ("/resource/article", "/resource/article/")),
    )
