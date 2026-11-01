"""Location fields in persisted records, distinct from prose and message text.

Storage uses area references; runtime consumers receive native absolute paths.
The same field vocabulary is used by the one-time database conversion.
"""
import os
from pathlib import Path

PATH_KEYS = {'path', 'paths', 'cwd', 'root', 'directory', 'directories', 'folder', 'file',
             'canonical_checkout', 'canonical_root', 'git_common_dir', 'account_dir',
             'working_directory', 'entrypoint', 'source_path', 'target_path', 'from', 'to',
             'evidence_paths', 'artifact_root', 'file_path', 'location'}
PROSE_KEYS = {'text', 'content', 'body', 'markdown', 'prompt', 'instructions', 'script',
              'summary', 'summary_text', 'description', 'goal', 'message', 'html', 'log',
              'stderr_tail', 'stdout_tail', 'error', 'traceback', 'raw_response'}


def path_key(key):
    return isinstance(key, str) and (key in PATH_KEYS or key.endswith(('_path', '_paths', '_directory', '_dir', '_root', '_file')))


def transform(value, locate, key=''):
    if isinstance(value, dict):
        return {k: v if k in PROSE_KEYS else transform(v, locate, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [transform(v, locate, key) for v in value]
    if isinstance(value, str) and path_key(key):
        return locate(value)
    return value


def stored_record(value):
    if not os.environ.get('PORTAL_INSTANCE_ROOT'):
        return value
    from .instance_locations import current_instance
    def locate(text):
        if not text.startswith('/') or '\n' in text or '\0' in text:
            return text
        path = Path(text).resolve()
        instance = current_instance()
        from .instance_manifest import AREAS
        for area in AREAS:
            root = instance.area(area)
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                return area + ':' + ('' if relative == '.' else relative)
        return text
    return transform(value, locate)


def located_record(value):
    from .instance_locations import located_path
    return transform(value, located_path)
