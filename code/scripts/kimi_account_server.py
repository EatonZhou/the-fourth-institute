"""Native Kimi account host, using the selected instance and source tree."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.kimi_code_server import main

if __name__ == '__main__':
    try:
        status = main(sys.argv[1:])
    except Exception as error:
        # Startup exceptions can contain private database or provider context.
        print('Kimi account host failed (' + type(error).__name__ + ')', file=sys.stderr)
        status = 1
    raise SystemExit(status)
