"""This Portal Web process: its instance name, build ID, service name and start time."""
from __future__ import annotations

import os
import time


_PORTAL_INSTANCE = os.getenv("PORTAL_INSTANCE", "dev").strip() or "dev"
_PORTAL_BUILD_ID = os.getenv("PORTAL_BUILD_ID", "").strip() or "development"

_PORTAL_SERVICE_NAME = os.getenv("PORTAL_SERVICE_NAME", "management-portal.service").strip() or "management-portal.service"

_SERVER_STARTED_AT = time.time()
