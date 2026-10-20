"""Synthetic installation aliases for read-compatibility tests only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

ALIASES = {'PORTAL_SHARE_ROOT': ['ARCHIVE_SHARE_ROOT'], 'PORTAL_PROJECTS_ROOT': ['ARCHIVE_PROJECTS_ROOT'], 'PORTAL_EXECUTION_CONTEXT_FILE': ['ARCHIVE_EXECUTION_CONTEXT_FILE'], 'PORTAL_RESOURCE_BACKUP_ROOT': ['ARCHIVE_RESOURCE_BACKUP_ROOT'], 'PORTAL_RESIDENT_ID': ['ARCHIVE_RESIDENT_ID'], 'PORTAL_LINEAGE_ID': ['ARCHIVE_LINEAGE_ID'], 'PORTAL_EXECUTION_ID': ['ARCHIVE_EXECUTION_ID'], 'PORTAL_RESIDENT_EXECUTION_ID': ['ARCHIVE_RESIDENT_EXECUTION_ID'], 'PORTAL_GIT_CREDENTIAL_FILE': ['ARCHIVE_GIT_CREDENTIAL_FILE'], 'PORTAL_AI_RELAY_HOST': ['ARCHIVE_AI_RELAY_HOST'], 'PORTAL_AI_RELAY_PORT': ['ARCHIVE_AI_RELAY_PORT'], 'PORTAL_AI_RELAY_ALLOWED_IPS': ['ARCHIVE_AI_RELAY_ALLOWED_IPS'], 'PORTAL_AI_RELAY_MAX_BYTES': ['ARCHIVE_AI_RELAY_MAX_BYTES'], 'PORTAL_AI_RELAY_MAX_CONCURRENT': ['ARCHIVE_AI_RELAY_MAX_CONCURRENT'], 'PORTAL_REPO_CREDENTIAL_ROOT': ['ARCHIVE_REPO_CREDENTIAL_ROOT'], 'PORTAL_REPO_CREDENTIAL_KEY_PATH': ['ARCHIVE_REPO_CREDENTIAL_KEY_PATH'], 'PORTAL_SSH_KEY_ROOT': ['ARCHIVE_SSH_KEY_ROOT'], 'PORTAL_DEVICE_STATUS_PATH': ['ARCHIVE_DEVICE_STATUS_PATH'], 'PORTAL_DEVICE_SECRET_ROOT': ['ARCHIVE_DEVICE_SECRET_ROOT'], 'PORTAL_SSH_PASSWORD_FILE': ['ARCHIVE_SSH_PASSWORD_FILE'], 'PORTAL_ARTICLE_BUILD_ROOT': ['ARCHIVE_ARTICLE_BUILD_ROOT'], 'PORTAL_RESOURCE_AUTO_REGISTER_STATE_PATH': ['ARCHIVE_RESOURCE_AUTO_REGISTER_STATE_PATH'], 'PORTAL_RESOURCE_AUTO_REGISTER': ['ARCHIVE_RESOURCE_AUTO_REGISTER'], 'PORTAL_RESOURCE_AUTO_REGISTER_INTERVAL_SECONDS': ['ARCHIVE_RESOURCE_AUTO_REGISTER_INTERVAL_SECONDS'], 'PORTAL_RESOURCE_AUTO_REGISTER_INITIAL_DELAY_SECONDS': ['ARCHIVE_RESOURCE_AUTO_REGISTER_INITIAL_DELAY_SECONDS'], 'PORTAL_RESOURCE_ID_ACTOR': ['ARCHIVE_RESOURCE_ID_ACTOR'], 'PORTAL_RESOURCE_ID_REEXEC': ['ARCHIVE_RESOURCE_ID_REEXEC'], 'PORTAL_URL': ['ARCHIVE_PORTAL_URL'], 'PORTAL_ROOT': ['ARCHIVE_PORTAL_ROOT'], 'PORTAL_PYTHON': ['ARCHIVE_PORTAL_PYTHON'], 'PORTAL_AUTHORIZATION_CONTEXT_FILE': ['ARCHIVE_AUTHORIZATION_CONTEXT_FILE']}
_CONTEXTS = []

@contextmanager
def synthetic_site(domain_values=None):
    with tempfile.TemporaryDirectory(prefix="tfi-compatibility-") as directory:
        path = Path(directory) / "site.json"
        values = {
            "service_invocation_plan_schemas": ["archive.service-invocation-plan.v1"],
            "runtime_identity_header_aliases": {"X-Portal-Execution-ID": ["X-Archive-Execution-ID"]},
            "execution_id_header": "X-Archive-Execution-ID",
            "seed_state_markers": ["archive.seed-state.v1"],
            "architecture_source_prefixes": ["archive."],
            "resident_identity_fingerprint_names": {"PORTAL_RESIDENT_ID": "ARCHIVE_RESIDENT_ID", "PORTAL_LINEAGE_ID": "ARCHIVE_LINEAGE_ID"},
        }
        values.update(domain_values or {})
        path.write_text(json.dumps({"deployment": {"compatibility": {
            "format_namespaces": ["archive."], "environment_aliases": ALIASES,
            "domain_values": values,
        }}}))
        with patch.dict(os.environ, {"PORTAL_SITE_CONFIG": str(path)}):
            yield path

def setUpModule():
    context = synthetic_site()
    path = context.__enter__()
    _CONTEXTS.append((context, path))

def tearDownModule():
    context, _ = _CONTEXTS.pop()
    context.__exit__(None, None, None)

def site_environ(values):
    """Keep the test's explicit site file when testing a cleared environment."""
    if not _CONTEXTS:
        raise RuntimeError("Synthetic site fixture has not started")
    return {"PORTAL_SITE_CONFIG": str(_CONTEXTS[-1][1]), **values}
