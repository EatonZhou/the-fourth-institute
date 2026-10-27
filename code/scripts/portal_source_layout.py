"""Source ownership for Portal's shared runtime and separately deployed Web."""
from pathlib import Path
import importlib.abc
import importlib.machinery
import os
import sys

SHARED_PATHS = (
    'management_common', 'management_portal/execution',
    'management_portal/resources', 'management_portal/society_service',
    'management_portal/hatg',
)

_INSTALLATION_ROOT = Path(__file__).resolve().parents[1]
if str(_INSTALLATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_INSTALLATION_ROOT))


class SharedSourceFinder(importlib.abc.MetaPathFinder):
    """Route only shared package roots; child imports follow their package path."""

    def __init__(self, root: Path, paths: tuple[str, ...]):
        self.packages = {name.replace('/', '.'): root / name for name in paths}

    def find_spec(self, fullname, path=None, target=None):
        package = self.packages.get(fullname)
        if package is None:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, [str(package.parent)])
        if spec is None:
            raise ImportError(f'Shared source package is unavailable: {package}')
        return spec


def activate_shared_imports(worktree: Path) -> Path | None:
    """Route shared packages to ``PORTAL_SHARED_SOURCE_ROOT`` when the pair sets it."""
    configured = os.environ.get('PORTAL_SHARED_SOURCE_ROOT', '').strip()
    if not configured:
        return None
    root = Path(configured)
    for finder in sys.meta_path:
        if isinstance(finder, SharedSourceFinder):
            return root
    sys.meta_path.insert(0, SharedSourceFinder(root, SHARED_PATHS))
    return root


# Select the owner using only stdlib before even configuration/path helpers are
# imported. Otherwise importing this module caches Release's management_common
# before management_portal.__init__ can install the shared package finder.
_CONFIGURED_SHARED_ROOT = activate_shared_imports(_INSTALLATION_ROOT)
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config

SHARED_ROOT = _CONFIGURED_SHARED_ROOT or resolve_directory(
    read_site_config().get('deployment', {}).get('source_root', str(_INSTALLATION_ROOT)))
# These packages own shared persisted facts and their RPC contracts. Both Web
# clients see the same contracts; both service pools execute from SHARED_ROOT.
COMPONENTS = {
    'web': {'unit': None, 'build_key': 'web', 'module': 'management_portal.asgi'},
    'resource': {'unit': 'resource-service', 'build_key': 'resource_service', 'module': 'management_portal.resources.server'},
    'execution-service': {'unit': 'execution-service', 'build_key': 'execution_service', 'module': 'management_portal.execution.service'},
    'execution-worker': {'unit': 'execution-worker', 'build_key': 'execution_worker', 'module': 'management_portal.execution.worker_manager'},
    'lineage': {'unit': 'society-service', 'build_key': 'society_service', 'module': 'management_portal.society_service.server'},
}
SHARED_COMPONENTS = tuple(name for name, item in COMPONENTS.items() if item['unit'])
# Unit names of the source-checkout pools; one-time migrations still check them.
INSTANCE_LAYOUT = {}
for _instance, _web, _port in (
    ('dev', 'management-portal.service', 18086),
    ('release', 'management-portal-release.service', 18085),
):
    _units = {name: (f'portal-{_instance}-{item["unit"]}.service' if item['unit'] else _web)
              for name, item in COMPONENTS.items()}
    INSTANCE_LAYOUT[_instance] = {
        'target': f'portal-{_instance}-execution.target', 'web': _web, 'port': _port,
        'component_units': _units,
        'services': (f'portal-{_instance}-nats.service', *(_units[name] for name in SHARED_COMPONENTS)),
    }
SHARED_MODULES = {item['unit']: (item['module'], item['build_key'])
                  for name, item in COMPONENTS.items() if name in SHARED_COMPONENTS}
