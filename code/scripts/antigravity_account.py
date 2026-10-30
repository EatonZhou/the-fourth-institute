#!/usr/bin/env python3
"""The isolated Antigravity OAuth subprocess used by Portal's login manager."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_portal.antigravity_accounts import main

if __name__ == "__main__":
    raise SystemExit(main())
