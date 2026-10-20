#!/bin/sh
# The caller supplies PORTAL_PYTHON; Portal's private reader validates the file.
exec "${PORTAL_PYTHON:-python3}" - "$0" <<'PY'
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[1]))
    from management_common.product_environment import read_product_env
    from management_common.file_permissions import read_private_bytes

    filename = read_product_env("PORTAL_SSH_PASSWORD_FILE")
    if not filename:
        raise ValueError("missing credential path")
    payload = read_private_bytes(Path(filename), required_mode=0o600)
    sys.stdout.buffer.write(payload)
except (OSError, ValueError):
    # SSH consumes stdout. Errors must not expose the secret or its path.
    raise SystemExit(1)
PY
