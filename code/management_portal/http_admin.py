"""Administrative HTTP actions for Portal access."""
from __future__ import annotations
import sys
import logging
import os
from pathlib import Path
from urllib.parse import parse_qs
from management_common import portal_network, portal_paths
from management_portal.http_routes import RouteDefinition
from scripts.migrate_portal_paths import job_status, queue_migration

class AdminHttp:

    def __init__(self, *, respond, session_actor, access_payload, read_json, is_admin):
        self.respond = respond
        self.session_actor = session_actor
        self.access_payload = access_payload
        self.read_json = read_json
        self.is_admin = is_admin

    def _require_admin(self, request):
        actor = self.session_actor(request)
        if not actor or not self.is_admin(actor):
            raise PermissionError('Portal administrator account required')

    def instances_get(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            from scripts.tfi_instance_manager import command
            config = os.environ.get("PORTAL_PATHS_CONFIG")
            current = str(Path(config).resolve().parent.parent) if config else ""
            return self.respond(request, 200, command("list", current=current))
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except Exception as exc:
            return self.respond(request, 503, {"error": str(exc)})

    def instances_post(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            payload = self.read_json(request)
            if (not isinstance(payload, dict) or set(payload) != {"name", "action"}
                    or payload["action"] not in ("start", "stop")
                    or not isinstance(payload["name"], str)):
                raise ValueError("Only an instance name and start/stop action are accepted")
            from scripts.tfi_instance_manager import command
            return self.respond(request, 202, command(payload["action"], name=payload["name"]))
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.respond(request, 503, {"error": str(exc)})

    def network_get(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            return self.respond(request, 200, portal_network.read_settings())
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception:
            return self.respond(request, 500, {'error': '无法读取网络设置'})

    def network_post(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        return self.respond(request, 409, {'error': 'Standalone TFI network settings are owned by install_tfi_release target configuration', 'supported': False})

    def embeddings_get(self, request, _parsed, _actor):
        return self.respond(request, 404, {'error': 'This distribution does not include Resident semantic search'})

    def embeddings_post(self, request, _parsed, _actor):
        return self.respond(request, 404, {'error': 'This distribution does not include Resident semantic search'})

    def embeddings_install_post(self, request, _parsed, _actor):
        return self.respond(request, 404, {'error': 'This distribution does not include Resident semantic search'})

    def wall_scheduling_get(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            from management_portal import wall_backend
            return self.respond(request, 200, wall_backend.scheduling_status())
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception:
            return self.respond(request, 503, {'error': '无法读取 WALL 定时任务状态'})

    def wall_scheduling_post(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            payload = self.read_json(request)
            if set(payload) != {'enabled'}:
                raise ValueError('只接受 enabled 开关')
            from management_portal import wall_backend
            return self.respond(request, 200, wall_backend.set_scheduling_enabled(payload['enabled']))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception:
            return self.respond(request, 503, {'error': '无法保存 WALL 定时任务状态'})

    def feature_switch_get(self, request, parsed, _actor):
        try:
            self._require_admin(request)
            from management_portal import portal_feature_switches
            name = parse_qs(parsed.query).get('name', [''])[0]
            return self.respond(request, 200, portal_feature_switches.status(name, self.session_actor(request)))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            logging.exception('Failed to read Portal feature switch')
            return self.respond(request, 503, {'error': str(exc)})

    def feature_switch_post(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            payload = self.read_json(request)
            if not isinstance(payload, dict) or set(payload) != {'name', 'enabled'}:
                raise ValueError('只接受 name 和 enabled')
            from management_portal import portal_feature_switches
            return self.respond(request, 200, portal_feature_switches.set_enabled(payload['name'], payload['enabled'], self.session_actor(request)))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            logging.exception('Failed to update Portal feature switch')
            return self.respond(request, 503, {'error': str(exc)})

    def paths_get(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            return self.respond(request, 200, {'paths': portal_paths.path_values(), 'resolved': {key: str(value) for key, value in portal_paths.paths().items()}})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            return self.respond(request, 500, {'error': str(exc)})

    def paths_post(self, request, _parsed, _actor):
        try:
            self._require_admin(request)
            payload = self.read_json(request)
            if payload.get('migrate') is not True:
                raise ValueError('Data migration confirmation is required')
            return self.respond(request, 202, queue_migration(payload.get('paths')))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except (ValueError, RuntimeError) as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.respond(request, 500, {'error': str(exc)})

    def paths_status_get(self, request, parsed, _actor):
        try:
            self._require_admin(request)
            job_id = parse_qs(parsed.query).get('id', [''])[0]
            return self.respond(request, 200, job_status(job_id))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except (OSError, ValueError) as exc:
            return self.respond(request, 404, {'error': str(exc)})

    def access_control_get(self, request, _parsed, _actor):
        actor = self.session_actor(request) or ''
        try:
            return self.respond(request, 200, self.access_payload(actor))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            print(f'Portal access-control read failed: {type(exc).__name__}', file=sys.stderr)
            return self.respond(request, 500, {'error': 'Unable to load access control.'})

    def routes(self):
        """The administration API."""
        return (
            RouteDefinition("GET", "/api/admin/access-control", self.access_control_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/network", self.network_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/instances", self.instances_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/paths", self.paths_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/embeddings", self.embeddings_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/wall-scheduling", self.wall_scheduling_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/feature-switch", self.feature_switch_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/paths/migration", self.paths_status_get, "portal-portal-get"),
            RouteDefinition("POST", "/api/admin/network", self.network_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/instances", self.instances_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/paths", self.paths_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/embeddings", self.embeddings_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/wall-scheduling", self.wall_scheduling_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/feature-switch", self.feature_switch_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/embeddings/install", self.embeddings_install_post, "portal-portal-post"),
        )
