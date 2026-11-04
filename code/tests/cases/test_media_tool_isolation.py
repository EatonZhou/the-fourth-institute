"""Portal loads and installs without external media-production tools."""
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[2]


def test_core_web_execution_and_permissions_load_without_speech_tools():
    program = '''
import importlib.abc, sys
attempts = []
class AbsentSpeech(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'edge_tts', 'mutagen', 'optional_tools'}:
            attempts.append(fullname)
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, AbsentSpeech())
from management_portal import web_server, asgi
from management_portal.hatg.catalog import instruction_catalog
from management_portal.execution import backend_services, isa
from management_portal.execution.contracts import EXECUTION_SERVICE_TYPES
from management_portal import home_explanation_media
from management_portal.permission_system.scheme.compiler import compile_scheme
from management_portal.permission_system.scheme.repository import builtin_documents
assert asgi.app is not None
assert compile_scheme(builtin_documents()['default']) is not None
assert EXECUTION_SERVICE_TYPES == {'agent_session', 'chat_completion'}
assert set(backend_services.default_execution_services()) == EXECUTION_SERVICE_TYPES
assert not {'GenerateSpeech', 'GetSpeechGeneration', 'ListSpeechProviders'} & instruction_catalog().keys()
# Even an import attempt swallowed by a fallback would violate this boundary.
assert attempts == [], attempts
assert not any(k.split('.')[0] in {'edge_tts', 'mutagen', 'optional_tools'} for k in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', program], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_core_installation_does_not_require_speech_packages():
    import runpy
    bootstrap = runpy.run_path(str(ROOT/'scripts/bootstrap_portal'))
    packages = bootstrap['declared_requirements'](ROOT/'requirements-portal-runtime.txt')
    assert not {'edge-tts', 'mutagen'} & packages.keys()


