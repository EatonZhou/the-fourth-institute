"""Exclude installation-specific Skills by their owner's source metadata.

Set ``metadata.distribution: site_only`` in SKILL.md to exclude the whole
Skill directory, including scripts and references. Explicit selection and
parent-directory selection cannot override this rule. Unmarked general and
core Skills retain the existing selection rules.
"""
from pathlib import Path

import yaml


class SkillScope:
    """One export's cache; never retain classification across export runs."""

    def __init__(self):
        self._directories = {}

    def excluded_root(self, path):
        try:
            path = Path(path).resolve()
        except (OSError, RuntimeError):
            # The normal file-scope checks diagnose missing/cyclic links.
            return None
        directory = path if path.is_dir() else path.parent
        return self._excluded_directory(directory)

    def _excluded_directory(self, directory):
        if directory in self._directories:
            return self._directories[directory]
        parent = (self._excluded_directory(directory.parent)
                  if directory.parent != directory else None)
        result = parent
        marker = directory / 'SKILL.md'
        if result is None and marker.is_file():
            # Read metadata only, never device instructions or credentials.
            with marker.open(encoding='utf-8') as stream:
                if stream.readline().strip() == '---':
                    header = []
                    size = 0
                    for line in stream:
                        size += len(line)
                        if size > 65536:
                            raise ValueError('Skill distribution metadata exceeds limit')
                        if line.strip() == '---':
                            break
                        header.append(line)
                    else:
                        raise ValueError('Unterminated Skill distribution metadata')
                    data = yaml.safe_load(''.join(header)) or {}
                    metadata = data.get('metadata', {}) if isinstance(data, dict) else {}
                    if isinstance(metadata, dict) and metadata.get('distribution') == 'site_only':
                        result = directory
        self._directories[directory] = result
        return result


def excluded_skill_ids(index, identities):
    """Use registered content roots, not private IDs or name heuristics."""
    from .content import registered_content_path
    from .core_skills import canonical_resources
    canonical = canonical_resources()
    scope = SkillScope()
    return {identity for identity in identities
            if index.get(identity, {}).get('resource_type') == 'skill'
            and scope.excluded_root(canonical[identity] if identity in canonical
                                    else registered_content_path(index[identity])) is not None}
