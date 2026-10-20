"""Resolve selected file trees without implicitly following external links."""
from pathlib import Path
import stat

from .sanitize import private_path
from .skill_scope import SkillScope


class FileScope:
    def __init__(self, files, policy=None):
        policy = policy or {}
        self.roots = tuple(Path(item['source']).absolute() for item in files
                           if not item.get('immutable_artifact'))
        self.excluded = self._rules(policy.get('excluded_paths', []))
        self.omitted = self._rules(policy.get('omitted_links', []))
        self.external = self._rules(policy.get('external_links', []), target=True)
        self.decisions = {}
        self.reset_roots = tuple(Path(root).resolve() for root in policy.get('reset_roots', []))
        self.skills = SkillScope()

    def _rules(self, rows, *, target=False):
        result = {}
        for row in rows:
            path = Path(row['path'])
            if not path.is_absolute() or '..' in path.parts or not self._inside(path):
                raise ValueError('File scope rule must name a path in the selected trees')
            if not str(row.get('reason', '')).strip():
                raise ValueError('File scope rule requires an explicit reason')
            if target and (not Path(row.get('target', '')).is_absolute()
                           or '..' in Path(row['target']).parts):
                raise ValueError('External link requires an exact absolute target')
            if str(path) in result:
                raise ValueError('Duplicate file scope rule')
            result[str(path)] = dict(row)
        return result

    def _inside(self, path):
        return any(path == root or path.is_relative_to(root) for root in self.roots)

    def walk(self, source):
        """Yield (relative path, regular source, is_directory); never yield links."""
        source = Path(source).absolute()
        yield from self._walk(source, source, Path('.'), frozenset())

    def _walk(self, logical, actual, relative, ancestors):
        if self._reset(logical, actual):
            return
        excluded = next((rule for path, rule in self.excluded.items()
                         if logical == Path(path) or logical.is_relative_to(path)), None)
        if excluded:
            self.decisions[str(logical)] = {**excluded, 'action': 'excluded'}
            return
        if private_path(logical) or private_path(actual):
            return
        if self.skills.excluded_root(actual) is not None:
            self.decisions[str(logical)] = {'path': str(logical), 'action': 'excluded',
                'reason': 'Skill metadata.distribution is site_only'}
            return
        if actual.is_symlink():
            if str(logical) in self.omitted:
                self.decisions[str(logical)] = {**self.omitted[str(logical)], 'action': 'omitted_link'}
                return
            try:
                resolved = actual.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ValueError(f'Selected link is broken or cyclic: {logical}') from error
            if self._reset(logical, resolved):
                return
            if private_path(resolved):
                raise ValueError(f'Selected link points to excluded authentication/runtime content: {logical}')
            rule = self.external.get(str(logical))
            if not self._inside(resolved) and (not rule or rule['target'] != str(resolved)):
                raise ValueError(f'Selected link leaves the declared file scope: {logical} -> {resolved}')
            self.decisions[str(logical)] = {'path': str(logical), 'target': str(resolved),
                'action': 'materialized', 'reason': rule['reason'] if rule else 'target is already in selected file scope'}
            actual = resolved
        if actual.is_dir():
            identity = (actual.stat().st_dev, actual.stat().st_ino)
            if identity in ancestors:
                raise ValueError(f'Selected directory link creates a cycle: {logical}')
            yield relative, actual, True
            for child in sorted(actual.iterdir()):
                yield from self._walk(logical / child.name, child, relative / child.name, ancestors | {identity})
        elif actual.is_file():
            yield relative, actual, False
        else:
            if actual.exists() and (stat.S_ISSOCK(actual.stat().st_mode) or stat.S_ISFIFO(actual.stat().st_mode)):
                self.decisions[str(logical)] = {'path': str(logical), 'action': 'excluded',
                    'reason': 'local process socket or pipe is not a distributable file'}
                return
            raise ValueError(f'Selected content is missing or not a regular file: {logical}')

    def _reset(self, logical, actual):
        if any(actual == root or actual.is_relative_to(root) for root in self.reset_roots):
            self.decisions[str(logical)] = {'path': str(logical), 'action': 'excluded',
                'reason': 'stage progress starts empty in the new distribution'}
            return True
        return False

    def report(self):
        return {'schema': 'tfi.file-scope.v1', 'symlinks': 'materialize only selected or explicitly resolved targets',
                'decisions': [self.decisions[key] for key in sorted(self.decisions)]}


def preflight(files, policy=None):
    scope = FileScope(files, policy)
    count = 0
    for item in files:
        if item.get('immutable_artifact'):
            continue
        for _, _, directory in scope.walk(item['source']):
            count += not directory
    return {**scope.report(), 'regular_files': count}


def external_path_mappings(files, policy=None):
    """Map reviewed physical targets to an included logical copy, never /data."""
    scope = FileScope(files, policy)
    result = {}
    for row in scope.external.values():
        link = Path(row['path'])
        candidates = []
        for item in files:
            if item.get('immutable_artifact'):
                continue
            source = Path(item['source']).absolute()
            if link != source and not link.is_relative_to(source):
                continue
            target = Path(item['target']) / link.relative_to(source)
            if len(target.parts) < 3 or target.parts[0] != 'data' or '..' in target.parts:
                raise ValueError('External file mapping must stay inside a declared data root')
            replacement = '${' + target.parts[1] + '}/' + Path(*target.parts[2:]).as_posix()
            candidates.append((-len(source.parts), replacement))
        if not candidates:
            raise ValueError('External link has no selected destination')
        replacement = min(candidates)[1]
        # Multiple logical aliases of one target use one deterministic reference.
        result[row['target']] = min(result.get(row['target'], replacement), replacement)
    return sorted(result.items())
