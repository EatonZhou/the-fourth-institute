"""Paper library, review, collector, and asset HTTP routes."""

import re
from pathlib import Path
from urllib.parse import parse_qs

from management_portal import (
    page_api_auth, paper_ccf, paper_ccf_enrichment, paper_collection_jobs, paper_detail, paper_favorites,
    paper_figure_jobs, paper_figures, paper_library, paper_notes, paper_reading_jobs, paper_refs,
    paper_registration, paper_resources, paper_reviews, semantic_search_jobs,
)
from management_portal.http_routes import ROUTE_NOT_FOUND, RouteDefinition
from management_portal.paper_collector import authenticated_request

ROOT = Path(__file__).resolve().parent


def authorize_api(request, auth, *, respond):
    """The actor of a Paper API caller, or "" after answering 403/503."""
    try:
        return page_api_auth.authorize(auth)
    except PermissionError as exc:
        respond(request, 403, {"error": str(exc)})
    except Exception:
        respond(request, 503, {"error": "Paper API 身份校验暂不可用"})
    return ""


def payload_project_ids(payload: dict) -> list[str]:
    values: list[object] = []
    raw_project_ids = payload.get("project_ids")
    if isinstance(raw_project_ids, list):
        values.extend(raw_project_ids)
    elif isinstance(raw_project_ids, str):
        values.extend(re.split(r"[,;\n]", raw_project_ids))
    values.extend(
        payload.get(key)
        for key in ("project", "project_id", "session")
        if payload.get(key) not in (None, "")
    )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        project_id = str(value or "").strip()
        key = project_id.casefold()
        if not project_id or key in seen:
            continue
        seen.add(key)
        result.append(project_id)
    return result


class PaperHttp:
    def __init__(self, *, respond, exception_response, media_file, get_auth,
                 authorize, read_json,
                 read_multipart, multipart_limit, body_too_large,
                 require_resource, require_active_actor, project_ids,
                 notify_index):
        self.respond = respond
        self.exception_response = exception_response
        self.media_file = media_file
        self.get_auth = get_auth
        self.authorize = authorize
        self.read_json = read_json
        self.read_multipart = read_multipart
        self.multipart_limit = multipart_limit
        self.body_too_large = body_too_large
        self.require_resource = require_resource
        self.require_active_actor = require_active_actor
        self.project_ids = project_ids
        self.notify_index = notify_index

    @staticmethod
    def _param(qs, key):
        return str((qs.get(key) or [""])[0]).strip()

    @staticmethod
    def _query_params(qs):
        return {key: values[0] if values else "" for key, values in qs.items()}

    def get_route(self, operation):
        def route(request, parsed, _route_actor):
            auth = self.get_auth(request)
            if not auth:
                return self.respond(request, 401, {"error": "agent token or human session required"})
            actor = self.authorize(request, auth)
            if not actor:
                return None
            qs = parse_qs(parsed.query)
            try:
                return operation(request, qs, actor)
            except KeyError as exc:
                return self.respond(request, 404, {"error": str(exc) or "not found"})
            except FileNotFoundError as exc:
                return self.respond(request, 404, {"error": str(exc) or "asset not found"})
            except PermissionError as exc:
                return self.respond(request, 403, {"error": str(exc)})
            except ValueError as exc:
                return self.respond(request, 400, {"error": str(exc)})
            except Exception as exc:
                return self.exception_response(request, exc)
        return route

    def post_route(self, operation, *, registration=False, upload_limit=None):
        def route(request, parsed, _route_actor):
            auth = self.get_auth(request)
            if not auth:
                return self.respond(request, 401, {"error": "agent token or human session required"})
            actor = self.authorize(request, auth)
            if not actor:
                return None
            if upload_limit and str(request.headers.get("Content-Type") or "").casefold().startswith("multipart/form-data"):
                try:
                    payload = self.read_multipart(
                        request,
                        max_bytes=self.multipart_limit(upload_limit()),
                    )
                except self.body_too_large as exc:
                    return self.respond(request, 413, {"error": str(exc)})
            else:
                payload = self.read_json(request)
            try:
                paper_id = str(payload.get("paper_id") or "").strip()
                project_ids = self.project_ids(payload)
                if registration and project_ids:
                    payload["project_ids"] = project_ids
                    if not str(payload.get("session") or "").strip():
                        payload["session"] = project_ids[0]
                if paper_id.startswith("paper_"):
                    self.require_resource(paper_id, actor)
                elif project_ids:
                    self.require_active_actor(actor)
                return operation(request, payload, auth, actor)
            except KeyError as exc:
                return self.respond(request, 404, {"error": str(exc) or "not found"})
            except PermissionError as exc:
                return self.respond(request, 403, {"error": str(exc)})
            except ValueError as exc:
                return self.respond(request, 400, {"error": str(exc)})
            except Exception as exc:
                return self.exception_response(request, exc)
        return route

    def get_query_backend(self, module, method_name):
        return self.get_route(lambda request, qs, actor: self.respond(
            request, 200, getattr(module, method_name)(self._query_params(qs)),
        ))

    def post_backend(self, module, method_name, *, upload_limit=None):
        return self.post_route(
            lambda request, payload, auth, actor: self.respond(
                request, 200, getattr(module, method_name)(payload, actor),
            ), upload_limit=upload_limit,
        )

    def personal_post_route(self, operation):
        """Personal state skips the Paper and Project checks that post_route makes."""
        return self.get_route(lambda request, _qs, actor: operation(
            request, self.read_json(request), actor,
        ))

    def favorite(self, request, payload, actor):
        return self.respond(request, 200, paper_favorites.set_favorite(
            actor, payload.get("paper_id"), payload.get("favorite"),
        ))

    def library(self, request, qs, actor):
        return self.respond(request, 200, paper_library.list_library(self._query_params(qs), actor=actor))

    def detail(self, request, qs, actor):
        paper_id = self._param(qs, "id")
        if not paper_id:
            return self.respond(request, 400, {"error": "paper id required"})
        self.require_resource(paper_id, actor)
        sections = self._param(qs, "sections")
        return self.respond(request, 200, paper_detail.get_paper_detail(paper_id, sections or None))

    def registration_draft(self, request, qs, actor):
        upload_id = self._param(qs, "upload_id")
        if not upload_id:
            return self.respond(request, 400, {"error": "paper registration upload id required"})
        return self.respond(request, 200, paper_registration.get_paper_registration_draft(upload_id, actor))

    def review_status(self, request, qs, actor):
        job_id = self._param(qs, "job_id")
        paper_id = self._param(qs, "paper_id")
        if job_id and not paper_id:
            paper_id = paper_reviews.review_job_paper_id(job_id)
        if paper_id or job_id:
            self.require_resource(paper_id, actor)
        return self.respond(request, 200, paper_reviews.get_review_status(actor=actor, job_id=job_id, paper_id=paper_id))

    def reviews(self, request, qs, actor):
        paper_id = self._param(qs, "paper_id")
        self.require_resource(paper_id, actor)
        return self.respond(request, 200, paper_reviews.list_reviews(paper_id))

    def semantic_searches(self, request, qs, actor):
        return self.respond(request, 200, semantic_search_jobs.list_searches("paper", actor))

    def semantic_search(self, request, qs, actor):
        search_id = self._param(qs, "id")
        if not search_id:
            return self.respond(request, 400, {"error": "semantic search id required"})
        return self.respond(request, 200, semantic_search_jobs.get_search("paper", search_id, actor))

    def collector_jobs(self, request, qs, actor):
        with authenticated_request(self.get_auth(request)):
            result = paper_collection_jobs.list_paper_collection_jobs(actor)
        return self.respond(request, 200, result)

    def docs(self, request, qs, actor):
        return self.respond(request, 200, paper_library.api_docs())

    def deep_read_status(self, request, qs, actor):
        return self.respond(request, 200, paper_reading_jobs.get_deep_read_status(
            actor=actor, job_id=self._param(qs, "job_id"), paper_id=self._param(qs, "paper_id"),
        ))

    def exp_status(self, request, qs, actor):
        return self.respond(request, 200, paper_reading_jobs.get_exp_status(
            actor=actor, job_id=self._param(qs, "job_id"), paper_id=self._param(qs, "paper_id"),
        ))

    def asset(self, request, qs, actor):
        paper_id = self._param(qs, "id")
        kind = self._param(qs, "kind")
        if not paper_id or not kind:
            return self.respond(request, 400, {"error": "paper id and kind required"})
        self.require_resource(paper_id, actor)
        path, content_type = paper_detail.resolve_asset(
            paper_id, kind, asset_id=self._param(qs, "figure_id"),
            generated_id=self._param(qs, "generated_id"),
        )
        return self.media_file(request, path, content_type)

    def register(self, request, payload, auth, actor):
        result = paper_registration.register_paper(payload, actor)
        self.notify_index(
            resource_id=str((result.get("paper") or {}).get("id") or result.get("id") or ""),
            resource_type="paper", kind="created",
        )
        return self.respond(request, 200, result)

    def create_registration_draft(self, request, payload, auth, actor):
        return self.respond(request, 202, paper_registration.create_paper_registration_draft(payload, actor))

    def register_ccf(self, request, payload, auth, actor):
        result = paper_ccf.register_ccf_dblp_paper(payload, actor)
        self.notify_index(resource_id=str(result.get("id") or ""), resource_type="paper", kind="created")
        return self.respond(request, 200, result)

    def delete(self, request, payload, auth, actor):
        result = paper_resources.delete_paper(payload, actor)
        self.notify_index(resource_id=str(result.get("paper_id") or ""), resource_type="paper", kind="deleted")
        return self.respond(request, 200, result)

    def upload_pdf(self, request, payload, auth, actor):
        result = paper_registration.upload_paper_pdf(payload, actor)
        self.notify_index(
            resource_id=str(result.get("id") or payload.get("paper_id") or ""),
            resource_type="paper", kind="updated",
        )
        return self.respond(request, 200, result)

    def delete_pdf(self, request, payload, auth, actor):
        result = paper_registration.delete_paper_pdf(payload, actor)
        self.notify_index(
            resource_id=str(result.get("id") or payload.get("paper_id") or ""),
            resource_type="paper", kind="updated",
        )
        return self.respond(request, 200, result)

    def semantic_search_post(self, search_operation, kind):
        """A Paper Saved Search operation of ``semantic_search_jobs`` (start, refresh or delete)."""
        def operation(request, payload, auth, actor):
            result = search_operation("paper", payload, actor)
            self.notify_index(
                resource_id=str(result.get("search_id") or ""),
                resource_type="search", kind=kind,
            )
            return self.respond(request, 200, result)
        return self.post_route(operation)

    def collector_start(self, request, payload, auth, actor):
        with authenticated_request(auth):
            result = paper_collection_jobs.start_paper_collection(payload, actor)
        return self.respond(request, 200, result)


NAVIGATION = ({"id": "paper", "label": "Paper", "href": "/paper", "order": 40},)


def routes(portal):
    """The Paper page and API (``web.routes``); ``portal`` is the Web shell."""
    http = PaperHttp(
        respond=lambda *args: portal._serve_json(*args),
        exception_response=lambda request, exc: portal._serve_exception_json(request, exc),
        media_file=lambda *args: portal._serve_media_file(*args),
        get_auth=lambda request: portal._get_agent_api_auth(request),
        authorize=lambda request, auth: authorize_api(request, auth, respond=portal._serve_json),
        read_json=lambda request: portal._read_request_json(request),
        read_multipart=lambda request, **kwargs: portal._read_request_multipart(request, **kwargs),
        multipart_limit=lambda size: portal._multipart_body_limit(size),
        body_too_large=portal.RequestBodyTooLarge,
        require_resource=lambda *args: portal._require_resource(*args),
        require_active_actor=lambda *args: portal._require_active_actor(*args),
        project_ids=payload_project_ids,
        notify_index=lambda **kwargs: portal._notify_resource_index(**kwargs),
    )

    def unknown_api(request, parsed, _actor, *, method):
        if not parsed.path.startswith("/paper/api/"):
            return ROUTE_NOT_FOUND
        auth = portal._get_agent_api_auth(request)
        if not auth:
            return portal._serve_json(request, 401, {"error": "agent token or human session required"})
        if not authorize_api(request, auth, respond=portal._serve_json):
            return None
        if method == "POST":
            portal._read_request_json(request)
        return portal._serve_http_error(request, 404, "API route not found")

    def page(request, parsed, actor):
        return portal._serve_authenticated_document(request, 200, (ROOT / "paper.html").read_bytes())

    get, post, backend = http.get_route, http.post_route, http.post_backend

    def pdf_upload_limit():
        return paper_registration.PAPER_PDF_UPLOAD_MAX_BYTES

    def figure_image_limit():
        return paper_figure_jobs.PAPER_FIGURE_IMAGE_MAX_BYTES

    api = (
        ("GET", "library", get(http.library)),
        ("POST", "favorites", http.personal_post_route(http.favorite)),
        ("GET", "ccf/venues", http.get_query_backend(paper_ccf, "ccf_venues")),
        ("GET", "ccf/years", http.get_query_backend(paper_ccf, "ccf_years")),
        ("GET", "ccf/papers", http.get_query_backend(paper_ccf, "ccf_papers")),
        ("GET", "papers/detail", get(http.detail)),
        ("GET", "papers/registration-draft", get(http.registration_draft)),
        ("GET", "reviews/status", get(http.review_status)),
        ("GET", "reviews", get(http.reviews)),
        ("GET", "semantic-searches", get(http.semantic_searches)),
        ("GET", "semantic-search", get(http.semantic_search)),
        ("GET", "collector/jobs", get(http.collector_jobs)),
        ("GET", "docs", get(http.docs)),
        ("GET", "deep-read/status", get(http.deep_read_status)),
        ("GET", "exp/status", get(http.exp_status)),
        ("GET", "asset", get(http.asset)),
        ("POST", "papers/register", post(http.register, registration=True)),
        ("POST", "papers/registration-draft", post(http.create_registration_draft, upload_limit=pdf_upload_limit)),
        ("POST", "ccf/register", post(http.register_ccf, registration=True)),
        ("POST", "ccf/enrich", backend(paper_ccf_enrichment, "ccf_paper_enrichment")),
        ("POST", "papers/delete", post(http.delete)),
        ("POST", "papers/upload-pdf", post(http.upload_pdf, upload_limit=pdf_upload_limit)),
        ("POST", "papers/delete-pdf", post(http.delete_pdf)),
        ("POST", "reviews/create", backend(paper_reviews, "create_review")),
        ("POST", "reviews/start", backend(paper_reviews, "start_review")),
        ("POST", "reviews/delete", backend(paper_reviews, "delete_review")),
        ("POST", "exp/create", backend(paper_reading_jobs, "create_exp")),
        ("POST", "exp/start", backend(paper_reading_jobs, "start_exp")),
        ("POST", "notes/create", backend(paper_notes, "create_note")),
        ("POST", "notes/delete", backend(paper_notes, "delete_note")),
        ("POST", "refs/extract", backend(paper_refs, "extract_refs")),
        ("POST", "refs/register", backend(paper_refs, "register_ref")),
        ("POST", "figures/extract", backend(paper_figures, "extract_figures")),
        ("POST", "figures/explain", backend(paper_figure_jobs, "start_figure_explanation")),
        ("POST", "figures/generate", backend(paper_figure_jobs, "start_figure_draft")),
        ("POST", "figures/input-image", backend(paper_figure_jobs, "upload_figure_input_image", upload_limit=figure_image_limit)),
        ("POST", "figures/generated/delete", backend(paper_figure_jobs, "delete_generated_figure")),
        ("POST", "semantic-search/start", http.semantic_search_post(semantic_search_jobs.start_search, "created")),
        ("POST", "semantic-search/refresh", http.semantic_search_post(semantic_search_jobs.refresh_search, "updated")),
        ("POST", "semantic-search/delete", http.semantic_search_post(semantic_search_jobs.delete_search, "deleted")),
        ("POST", "collector/start", post(http.collector_start)),
        ("POST", "collector/delete", backend(paper_collection_jobs, "delete_paper_collection_job")),
        ("POST", "deep-read/start", backend(paper_reading_jobs, "start_deep_read")),
    )
    return (
        *(RouteDefinition(method, f"/paper/api/{path}", handler, "paper", phase="dual-auth")
          for method, path, handler in api),
        # Bearer callers of a missing Paper API get 401/403 before 404, like the routes above.
        RouteDefinition("GET", "/", lambda request, parsed, actor: unknown_api(request, parsed, actor, method="GET"),
                        "paper-not-found", phase="dual-auth", match="fallback"),
        RouteDefinition("POST", "/", lambda request, parsed, actor: unknown_api(request, parsed, actor, method="POST"),
                        "paper-not-found", phase="dual-auth", match="fallback"),
        *(RouteDefinition("GET", path, page, "portal-pages-get", page=True) for path in ("/paper", "/paper/", "/paper.html")),
    )
