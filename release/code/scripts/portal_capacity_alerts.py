#!/usr/bin/env python3
"""One capacity observation; scheduling and execution identity belong to Lineage."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_portal.capacity_alerts import run_once


def main():
    from portal_script import inputs, isa, set_output

    invocation = inputs()
    arguments = invocation.get("arguments") or {}
    result = run_once(isa, lineage_id=invocation["lineage_id"],
                      recipients=arguments["recipients"], overrides=arguments.get("policy"))
    set_output(result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
