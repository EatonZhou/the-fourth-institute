"""RunScript entry; --installation-plan prints ISA material without writing ISA."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation-plan', action='store_true')
    args = parser.parse_args(argv)
    from management_portal.directory_maintenance import workflow_operations, run
    if args.installation_plan:
        print(json.dumps({'operations': workflow_operations(Path(__file__).resolve())}, ensure_ascii=False, indent=2))
        return 0
    result = run()
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
