"""Repository automation and workspace HTTP routes."""

import re
from pathlib import Path
from urllib.parse import parse_qs

from management_portal import (
    http_auth, http_documents, http_request_body, http_responses, repo_workspace_ai,
    repo_workspace_auto_commit, repo_workspace_checkout, repo_workspace_descriptions, repo_workspace_files,
    repo_workspace_git, repo_workspace_history, repo_workspace_network, repo_workspace_operations,
    repo_workspace_overview, repo_workspace_statistics, web_resources,
)
from management_portal.http_routes import ROUTE_NOT_FOUND, RouteDefinition
from management_portal.resources import get_resource_service

ROOT = Path(__file__).resolve().parent


class RepoHttp:
    def __init__(self, *, session_actor, resource_service, read_json, require_resource,
                 notify_index, respond, exception_response):
        self.session_actor = session_actor
        self.resource_service = resource_service
        self.read_json = read_json
        self.require_resource = require_resource
        self.notify_index = notify_index
        self.respond = respond
        self.exception_response = exception_response

    def automation_status(self, request, parsed, route_actor):
        qs = parse_qs(parsed.query)
        actor = self.session_actor(request) or ''
        repo_ids = [value for raw in qs.get('repo_id') or [] for value in str(raw).split(',') if value]
        if not repo_ids:
            repo_ids = [str(item.get('resource_id') or '') for item in self.resource_service().query(resource_types=['repo'], actor=actor, limit=5000).get('resources') or []]
        elif actor:
            visible_repo_ids = {str(item.get('resource_id') or '') for item in self.resource_service().query(resource_types=['repo'], actor=actor, limit=5000).get('resources') or []}
            repo_ids = [repo_id for repo_id in repo_ids if repo_id in visible_repo_ids]
        return self.respond(request, 200, repo_workspace_overview.repo_automation_status(repo_ids))

    def workspace_get(self, request, parsed, route_actor, params):
        repo_id = params['repo_id']
        action = params.get('action', '')
        if not re.fullmatch('repo_[0-9a-f]{12}', repo_id) or action not in {'', 'tree', 'stats', 'file', 'search', 'commits', 'commit', 'blame', 'diff', 'credentials'}:
            return ROUTE_NOT_FOUND
        qs = parse_qs(parsed.query)
        actor = self.session_actor(request) or ''
        try:
            self.require_resource(repo_id, actor)
            if not action:
                result = repo_workspace_overview.repo_overview(repo_id)
            elif action == 'tree':
                result = repo_workspace_files.list_repo_tree(repo_id, str((qs.get('path') or [''])[0]))
            elif action == 'stats':
                result = repo_workspace_statistics.repo_statistics(repo_id, str((qs.get('path') or [''])[0]))
            elif action == 'file':
                result = repo_workspace_files.read_repo_file(repo_id, str((qs.get('path') or [''])[0]))
            elif action == 'search':
                result = repo_workspace_files.search_repo_files(repo_id, str((qs.get('q') or [''])[0]), limit=int(str((qs.get('limit') or ['120'])[0]) or 120))
            elif action == 'commits':
                result = repo_workspace_history.list_repo_commits(repo_id, path=str((qs.get('path') or [''])[0]), limit=int(str((qs.get('limit') or ['40'])[0]) or 40), offset=int(str((qs.get('offset') or ['0'])[0]) or 0), revision=str((qs.get('revision') or ['HEAD'])[0]))
            elif action == 'commit':
                result = repo_workspace_history.read_commit(repo_id, str((qs.get('sha') or [''])[0]))
            elif action == 'blame':
                result = repo_workspace_history.blame_repo_file(repo_id, str((qs.get('path') or [''])[0]))
            elif action == 'credentials':
                result = repo_workspace_network.list_repo_credentials(repo_id, actor=actor)
            else:
                result = repo_workspace_history.working_diff(repo_id, str((qs.get('path') or [''])[0]))
            return self.respond(request, 200, result)
        except KeyError:
            return self.respond(request, 404, {'error': 'repo not found'})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except repo_workspace_checkout.RepoWorkspaceConflict as exc:
            return self.respond(request, 409, {'error': str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except repo_workspace_git.GitOperationError as exc:
            return self.respond(request, 422, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def description_generate(self, request, parsed, route_actor):
        payload = self.read_json(request)
        actor = self.session_actor(request) or ''
        raw_repo_ids = payload.get('repo_ids') or []
        if isinstance(raw_repo_ids, str):
            raw_repo_ids = [raw_repo_ids]
        if not isinstance(raw_repo_ids, list):
            return self.respond(request, 400, {'error': 'repo_ids must be an array'})
        try:
            for repo_id in raw_repo_ids:
                self.require_resource(str(repo_id), actor)
            result = repo_workspace_descriptions.generate_repo_descriptions([str(value) for value in raw_repo_ids], force=bool(payload.get('force')), provider_id=str(payload.get('provider') or ''), actor=actor)
            if result.get('generated_count'):
                self.notify_index(resource_type='repo', kind='updated')
            return self.respond(request, 200, result)
        except (ValueError, KeyError) as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except repo_workspace_ai.RepoDescriptionGenerationError as exc:
            return self.respond(request, 502, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def workspace_post(self, request, parsed, route_actor, params):
        repo_id = params["repo_id"]
        action = params["action"]
        if not re.fullmatch(r"repo_[0-9a-f]{12}", repo_id) or action not in {
            "file", "entry", "move", "commit", "auto-commit", "branch",
            "sync", "discard", "credentials",
        }:
            return ROUTE_NOT_FOUND
        payload = self.read_json(request)
        actor = self.session_actor(request) or ''
        try:
            self.require_resource(repo_id, actor)
            if action == 'file':
                result = repo_workspace_files.write_repo_file(repo_id, str(payload.get('path') or ''), str(payload.get('content') or ''), expected_hash=str(payload.get('expected_hash') or ''))
                status = 200
            elif action == 'entry':
                operation = str(payload.get('operation') or 'create').strip()
                if operation == 'delete':
                    result = repo_workspace_files.delete_repo_file(repo_id, str(payload.get('path') or ''))
                    status = 200
                elif operation == 'create':
                    result = repo_workspace_files.create_repo_entry(repo_id, str(payload.get('path') or ''), kind=str(payload.get('kind') or 'file'), content=str(payload.get('content') or ''))
                    status = 201
                else:
                    raise ValueError('entry operation must be create or delete')
            elif action == 'move':
                result = repo_workspace_files.move_repo_entry(repo_id, str(payload.get('path') or ''), str(payload.get('destination') or ''))
                status = 200
            elif action == 'commit':
                result = repo_workspace_operations.commit_repo_changes(repo_id, str(payload.get('message') or ''), description=str(payload.get('description') or ''), actor=actor)
                status = 201
            elif action == 'auto-commit':
                result = repo_workspace_auto_commit.auto_commit_repo(repo_id, actor=actor, provider_id=str(payload.get('provider') or ''))
                status = 201 if result.get('committed') else 200
            elif action == 'branch':
                result = repo_workspace_operations.update_repo_branch(repo_id, str(payload.get('operation') or ''), str(payload.get('name') or ''), start_point=str(payload.get('start_point') or 'HEAD'))
                status = 200
            elif action == 'sync':
                raw_credential = payload.get('credential')
                credential = dict(raw_credential) if isinstance(raw_credential, dict) else None
                result = repo_workspace_operations.sync_repo(repo_id, str(payload.get('operation') or ''), remote=str(payload.get('remote') or ''), branch=str(payload.get('branch') or ''), actor=actor, credential=credential, remember_credential=bool(payload.get('remember_credential')))
                status = 200
            elif action == 'discard':
                result = repo_workspace_operations.discard_repo_changes(repo_id)
                status = 200
            else:
                if str(payload.get('operation') or '').strip() != 'delete':
                    raise ValueError('credential operation must be delete')
                result = repo_workspace_network.delete_repo_credential(repo_id, str(payload.get('credential_id') or ''), actor=actor)
                status = 200
            self.notify_index(resource_id=repo_id, resource_type='repo', kind='updated')
            return self.respond(request, status, result)
        except KeyError:
            return self.respond(request, 404, {'error': 'repo not found'})
        except FileNotFoundError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except FileExistsError as exc:
            return self.respond(request, 409, {'error': f'already exists: {exc}'})
        except repo_workspace_checkout.RepoWorkspaceConflict as exc:
            return self.respond(request, 409, {'error': str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except repo_workspace_git.GitAuthenticationRequired as exc:
            return self.respond(request, 409, {'error': str(exc), 'code': 'git_auth_required', 'auth_challenge': exc.challenge})
        except repo_workspace_git.GitOperationError as exc:
            return self.respond(request, 422, {'error': str(exc)})
        except repo_workspace_ai.CommitMessageGenerationError as exc:
            return self.respond(request, 502, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)


def routes(portal):
    """The Repo workspace page and API (``web.routes``); ``portal`` is the Web shell."""
    http = RepoHttp(
        session_actor=lambda request: http_auth._get_session_username(request),
        resource_service=lambda: get_resource_service(),
        read_json=lambda request: http_request_body._read_request_json(request),
        require_resource=lambda *args: web_resources._require_resource(*args),
        notify_index=lambda **kwargs: web_resources._notify_resource_index(**kwargs),
        respond=lambda *args: http_responses._serve_json(*args),
        exception_response=lambda request, exc: http_responses._serve_exception_json(request, exc),
    )

    def page(request, parsed, actor):
        return http_documents._serve_authenticated_document(request, 200, (ROOT / 'repo_workspace.html').read_bytes())

    return (
        RouteDefinition('GET', '/api/repos/automation/status', http.automation_status, 'portal-repo'),
        RouteDefinition('GET', '/api/repos/{repo_id}/workspace', http.workspace_get, 'portal-repo', match='template'),
        RouteDefinition('GET', '/api/repos/{repo_id}/workspace/{action}', http.workspace_get, 'portal-repo', match='template'),
        RouteDefinition('POST', '/api/repos/descriptions/generate', http.description_generate, 'portal-repo'),
        RouteDefinition('POST', '/api/repos/{repo_id}/workspace/{action}', http.workspace_post, 'portal-repo', match='template'),
        *(RouteDefinition('GET', path, page, 'portal-pages-get', page=True) for path in ('/resource/repo', '/resource/repo/')),
    )
