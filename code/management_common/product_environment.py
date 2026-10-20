"""Read and inject canonical PORTAL process environment names.

Site deployment values remain owned by site_config. Historical environment
alias settings do not participate in process environment lookup or injection.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping


_SUFFIXES = (
    'SHARE_ROOT', 'PROJECTS_ROOT', 'EXECUTION_CONTEXT_FILE', 'RESOURCE_BACKUP_ROOT',
    'RESIDENT_ID', 'LINEAGE_ID', 'EXECUTION_ID', 'RESIDENT_EXECUTION_ID',
    'GIT_CREDENTIAL_FILE', 'AI_RELAY_HOST', 'AI_RELAY_PORT', 'AI_RELAY_ALLOWED_IPS',
    'AI_RELAY_MAX_BYTES', 'AI_RELAY_MAX_CONCURRENT', 'REPO_CREDENTIAL_ROOT',
    'REPO_CREDENTIAL_KEY_PATH', 'SSH_KEY_ROOT', 'DEVICE_STATUS_PATH',
    'DEVICE_SECRET_ROOT', 'SSH_PASSWORD_FILE', 'ARTICLE_BUILD_ROOT',
    'RESOURCE_AUTO_REGISTER_STATE_PATH', 'RESOURCE_AUTO_REGISTER',
    'RESOURCE_AUTO_REGISTER_INTERVAL_SECONDS', 'RESOURCE_AUTO_REGISTER_INITIAL_DELAY_SECONDS',
    'RESOURCE_ID_ACTOR', 'RESOURCE_ID_REEXEC',
)
_CANONICAL_NAMES = frozenset('PORTAL_' + suffix for suffix in _SUFFIXES) | {
    'PORTAL_URL', 'PORTAL_ROOT', 'PORTAL_PYTHON',
}


def _validate_name(canonical_name: str) -> None:
    if canonical_name not in _CANONICAL_NAMES:
        raise ValueError('Unknown product environment name: ' + str(canonical_name))


def read_product_env(canonical_name: str, default: str | None = None, *,
                     environ: Mapping[str, str] | None = None) -> str | None:
    """Read only the canonical name, preserving an explicit empty value."""
    _validate_name(canonical_name)
    values = os.environ if environ is None else environ
    return values.get(canonical_name, default)


def mirror_product_env(environ: MutableMapping[str, str], canonical_name: str, value: str) -> None:
    """Inject only the authoritative canonical field."""
    _validate_name(canonical_name)
    if not isinstance(value, str):
        raise TypeError('Environment values must be strings')
    environ[canonical_name] = value
