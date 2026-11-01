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


def activate_shared_imports(worktree: Path) -> Path:
    """Select shared packages from the explicitly selected instance manifest.

    A process that runs outside any instance, such as assembling release
    content from a fresh checkout, imports this checkout's own packages.
    """
    if not os.environ.get('PORTAL_INSTANCE_ROOT'):
        return worktree
    from management_common.instance_manifest import load
    root = load().code_root()
    for finder in sys.meta_path:
        if isinstance(finder, SharedSourceFinder):
            if finder.packages['management_common'] != root / 'management_common':
                raise ValueError('Cannot change instance source ownership in a running process')
            return root
    if root.resolve() == worktree.resolve():
        return root
    # The small manifest reader is imported before this finder can be installed.
    # Route subsequent common imports through the same shared package owner.
    common = sys.modules.get('management_common')
    if common is not None:
        common.__path__ = [str(root / 'management_common')]
    sys.meta_path.insert(0, SharedSourceFinder(root, SHARED_PATHS))
    return root


SHARED_ROOT = activate_shared_imports(_INSTALLATION_ROOT)
# Restartable components; the shared ones carry their service unit suffix.
COMPONENTS = {'web': None, 'resource': 'resource-service', 'execution-service': 'execution-service',
              'execution-worker': 'execution-worker', 'lineage': 'society-service'}
