"""This Portal Web process: its deployment name, build ID, service name and start time."""
from __future__ import annotations

import os
import time


_PORTAL_DEPLOYMENT = os.getenv("PORTAL_DEPLOYMENT", "release").strip() or "release"
_PORTAL_BUILD_ID = os.getenv("PORTAL_BUILD_ID", "").strip() or "development"

from management_common.instance_manifest import load
_manifest = load()
if _PORTAL_DEPLOYMENT not in _manifest.deployments:
    raise ValueError('Web deployment is not registered in the selected instance')
_PORTAL_INSTANCE_NAME = _manifest.name
_PORTAL_SERVICE_NAME = _manifest.service_name('dev-web' if _PORTAL_DEPLOYMENT == 'dev' else 'web') + '.service'

_SERVER_STARTED_AT = time.time()
