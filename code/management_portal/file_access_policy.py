"""Authenticated file identity and upload-address policy, without file I/O.

Permission entry points and resource providers share this policy. Its facts
come only from deployment settings, the User store and Resident descriptors;
it must not import the permission engine or resource service/providers.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlsplit

from management_common.principal import actor_principal

_HANDLE = re.compile(r"upload_[0-9a-f]{24}\Z")
_URL_PATH = re.compile(r"/api/uploads/(upload_[0-9a-f]{24})\Z")


def require_identity(actor, *, descriptors=None):
    """Require an active registered User or a live Resident, without PAR."""
    try:
        identity = actor_principal(actor) if actor else None
    except ValueError:
        identity = None
    if identity is None or identity.kind not in {'user', 'resident'}:
        raise PermissionError('Authenticated User or Resident required')
    if identity.kind == 'user':
        from management_portal.identity_store import user_status
        # The account table is authoritative. User administration also changes
        # PAR and must not be a dependency of this read-only authentication rule.
        if user_status(identity.id) != 'active':
            raise PermissionError('An active registered User is required')
    else:
        from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
        try:
            resident = (descriptors or CollaborationDescriptorStore()).get(identity.id)
        except KeyError:
            resident = None
        if not resident or resident.get('status') == 'deleted':
            raise PermissionError('An active authenticated Resident is required')
    return identity


def _origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        raise ValueError('Upload URL requires an official Portal origin')
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)


def _origins():
    from management_common.instance_manifest import current_instance
    from management_common.site_config import read_site_config

    origins = {_origin(current_instance().internal_web_origin())}
    deployment = read_site_config().get('deployment', {})
    if not isinstance(deployment, dict):
        raise ValueError('deployment must be an object')
    auth = deployment.get('auth', {})
    if not isinstance(auth, dict):
        raise ValueError('deployment.auth must be an object')
    public = os.environ.get('PORTAL_PUBLIC_ORIGIN', auth.get('public_origin', ''))
    if not isinstance(public, str):
        raise ValueError('deployment.auth.public_origin must be a string')
    if public:
        origins.add(_origin(public))
    aliases = auth.get('public_origin_aliases', [])
    if not isinstance(aliases, list):
        raise ValueError('deployment.auth.public_origin_aliases must be a list of origins')
    for alias in aliases:
        if not isinstance(alias, str):
            raise ValueError('Public origin aliases must be strings')
        parsed = urlsplit(alias)
        if parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise ValueError('Public origin aliases cannot contain paths, queries or fragments')
        origins.add(_origin(alias))
    return origins


def file_reference(address):
    """Recognize an upload reference locally; reject malformed/foreign URLs."""
    if isinstance(address, Mapping):
        if 'upload_id' not in address:
            return None
        if set(address) != {'upload_id'}:
            raise ValueError('Upload file reference accepts only upload_id')
        handle = address['upload_id']
    elif isinstance(address, str):
        if not address.startswith(('http:', 'https:', '//', '/api/uploads/')):
            return None
        if any(ord(char) < 32 for char in address) or '\\' in address:
            raise ValueError('Invalid upload URL')
        parsed = urlsplit(address)
        match = _URL_PATH.fullmatch(parsed.path)
        if not match or parsed.fragment or '#' in address:
            raise ValueError('Invalid upload URL path or fragment')
        if parsed.netloc or parsed.scheme:
            if _origin(address) not in _origins():
                raise ValueError('Upload URL belongs to another Portal origin')
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if query and query != [('download', '1')]:
            raise ValueError('Unsupported upload URL query')
        handle = match.group(1)
    else:
        return None
    if not isinstance(handle, str) or not _HANDLE.fullmatch(handle):
        raise ValueError('Invalid upload handle')
    return {'upload_id': handle}


def authorize(address, *, actor):
    """Every active authenticated User/Resident may read an upload file."""
    reference = file_reference(address)
    if reference is None:
        raise ValueError('Expected an upload file reference')
    require_identity(actor)
    return reference
