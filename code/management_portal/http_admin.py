"""Administrative HTTP actions for Portal access."""
from __future__ import annotations
import sys
import logging
from urllib.parse import parse_qs
from pathlib import Path
from management_common import portal_network
from management_portal.http_routes import RouteDefinition


def _move_area_command(name: str, area: str, target: str) -> dict:
    """Submit through the controller in the service owner's environment, as start/stop do."""
    import json
    import subprocess
    from scripts.tfi_instance_manager import supervisor_environment
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'tfi_move_area.py'
    result = subprocess.run([sys.executable, str(script), 'submit', '--name', name, '--area', area,
                             '--target', target], env=supervisor_environment(),
                            capture_output=True, text=True, encoding="utf-8", timeout=135)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError('Area move controller returned no valid result') from None
    if result.returncode:
        raise ValueError(value.get('error', 'Area move failed'))
    return value


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
            from management_common.instance_manifest import load
            current = str(load().root)
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
        """The installation's network settings, read only: its target configuration owns them."""
        try:
            self._require_admin(request)
            return self.respond(request, 200, portal_network.read_settings())
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception:
            return self.respond(request, 500, {'error': '无法读取网络设置'})

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
        """The data directory page: instance root, area and fixed locations with free space."""
        try:
            self._require_admin(request)
            from management_common.instance_areas import area_report
            from management_common.instance_manifest import load
            return self.respond(request, 200, area_report(load(), measure=False))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            return self.respond(request, 500, {'error': str(exc)})

    def paths_usage_get(self, request, parsed, _actor):
        """One directory's size; measuring a large area takes a while."""
        try:
            self._require_admin(request)
            from management_common import instance_areas
            from management_common.instance_manifest import AREAS, FIXED_DIRECTORIES, load
            name = parse_qs(parsed.query).get('name', [''])[0]
            instance = load()
            if name in AREAS:
                path = instance.area(name)
            elif name in FIXED_DIRECTORIES:
                path = instance.directory(name)
            else:
                raise ValueError('Unknown data directory')
            return self.respond(request, 200, {'name': name, 'size': instance_areas.directory_size(path)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.respond(request, 500, {'error': str(exc)})

    def paths_post(self, request, _parsed, _actor):
        """Submit「移动区域」: only an area name and its new absolute location are accepted."""
        try:
            self._require_admin(request)
            payload = self.read_json(request)
            if (not isinstance(payload, dict) or set(payload) != {'area', 'target'}
                    or not all(isinstance(payload[key], str) for key in payload)):
                raise ValueError('Only an area and its new absolute location are accepted')
            from management_common.instance_manifest import load
            return self.respond(request, 202, _move_area_command(load().name, payload['area'], payload['target']))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.respond(request, 503, {'error': str(exc)})

    def paths_operation_get(self, request, _parsed, _actor):
        """The instance's current or last control operation, an area move included."""
        try:
            self._require_admin(request)
            from management_common.instance_manifest import load
            from scripts.tfi_instance_manager import operation
            return self.respond(request, 200, {'operation': operation(load().root)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            return self.respond(request, 503, {'error': str(exc)})

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
            RouteDefinition("GET", "/api/admin/paths/usage", self.paths_usage_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/paths/operation", self.paths_operation_get, "portal-portal-get"),
            RouteDefinition("POST", "/api/admin/paths", self.paths_post, "portal-portal-post"),
            RouteDefinition("GET", "/api/admin/wall-scheduling", self.wall_scheduling_get, "portal-portal-get"),
            RouteDefinition("GET", "/api/admin/feature-switch", self.feature_switch_get, "portal-portal-get"),
            RouteDefinition("POST", "/api/admin/instances", self.instances_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/wall-scheduling", self.wall_scheduling_post, "portal-portal-post"),
            RouteDefinition("POST", "/api/admin/feature-switch", self.feature_switch_post, "portal-portal-post"),
        )
