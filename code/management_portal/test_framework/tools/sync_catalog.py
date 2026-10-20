"""Check the Test Catalog that is built from the test sources.

Usage: python -m management_portal.test_framework.tools.sync_catalog [--apply] [--export PATH]

The catalog comes from the code (management_portal/test_framework/catalog_source.py), so a new,
renamed or moved test needs no registration.  This tool reports
- docstring fields the catalog rejects and sources it cannot parse;
- case_ids.json entries whose test is gone, and those that only follow a test
  moved to another file of its directory;
- live_cases.json definitions whose test is gone;
- tests whose title has no Chinese text (no docstring title, so the catalog
  shows the function name);
- design-point bindings whose WALL point no longer exists in its design
  document.  WALL derives a point ID from the point's label, so rewording the
  label drops the binding unless the point carries an explicit
  `<!-- wall: {"key": ...} -->` key.  Bindings whose document cannot be read
  here are counted as unchecked.

The last two are reported only and do not change the exit status.

--apply writes the moved entrypoints into case_ids.json and drops the entries
and Live definitions of removed tests.  --export writes the whole catalog as
one tfi.test-catalog.v2 document, for example as evidence of what
a run selected from.  Exits 1 while a catalog error remains.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common.product_environment import read_product_env
from management_common.portal_paths import path as portal_path, resolve_directory
from management_portal.test_framework.catalog import CatalogRepository, atomic_catalog_write, catalog_write_lock
from management_portal.test_framework.catalog_source import (build_catalog, case_ids_path, effective_case_ids, live_cases_path,
                                            read_case_ids)

PROJECTS_ROOT = resolve_directory(read_product_env("PORTAL_PROJECTS_ROOT", str(portal_path("share_root") / "Projects")))
CHINESE = re.compile(r"[一-鿿]")


def check(root: Path = ROOT, projects_root: Path = PROJECTS_ROOT) -> dict[str, Any]:
    catalog = build_catalog(root)
    pins = read_case_ids(root)
    current, orphans = effective_case_ids(pins, set(catalog.discovered))
    live = json.loads(live_cases_path(root).read_text(encoding="utf-8")) if live_cases_path(root).is_file() else {}
    stale, unchecked = design_point_bindings(catalog.cases, root, projects_root)
    return {
        "errors": catalog.errors,
        "moved_ids": {entry: case_id for entry, case_id in current.items() if entry not in pins},
        "orphan_ids": orphans,
        "orphan_live": sorted(set(live.get("implementations", {})) - catalog.discovered),
        "untitled": sorted(_entrypoint(case) for case in catalog.cases if not CHINESE.search(str(case.get("title") or ""))),
        "stale_points": stale,
        "unchecked_points": unchecked,
        "case_ids": current,
        "live": live,
    }


def _entrypoint(case: dict[str, Any]) -> str:
    implementations = case.get("implementations") or []
    return str(implementations[0].get("entrypoint") or case["id"]) if implementations else case["id"]


def design_point_bindings(cases: list[dict[str, Any]], root: Path,
                          projects_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Split the bound cases into stale and unchecked bindings, keyed by case ID."""
    points: dict[str, set[str] | None] = {}
    stale: dict[str, str] = {}
    unchecked: dict[str, str] = {}
    for case in cases:
        ref = case.get("design_point_ref")
        if not ref:
            continue
        source = ref.rpartition("#")[0]
        if source not in points:
            points[source] = _design_points(source, root, projects_root)
        if points[source] is None:
            unchecked[case["id"]] = ref
        elif ref not in points[source]:
            stale[case["id"]] = ref
    return stale, unchecked


def _design_points(source: str, root: Path, projects_root: Path) -> set[str] | None:
    """The point IDs WALL finds in a design document, or None when it cannot be read here."""
    from management_portal import wall_documents as wall

    if wall.RESOURCE_ID.fullmatch(source):
        # Knowledge items are <project>/knowledge_base/<type>/<date>__<type>__<title>__<id>.md.
        path = next((path for path in sorted(projects_root.glob(f"*/knowledge_base/*/*__{source}.md"))
                     if not any(part.startswith(".") for part in path.relative_to(projects_root).parts)), None)
    else:
        path = root / source
    try:
        text = path.read_text(encoding="utf-8") if path else None
    except OSError:
        text = None
    if text is None:
        return None
    return {point["id"] for point in wall.parse_points(wall.Source(source, "", "", "design"), text)}


def apply(result: dict[str, Any], root: Path = ROOT) -> None:
    path = case_ids_path(root)
    with catalog_write_lock(path):
        atomic_catalog_write(path, {"schema": "tfi.test-case-ids.v1", "ids": dict(sorted(result["case_ids"].items()))})
    if result["orphan_live"]:
        live = dict(result["live"])
        live["implementations"] = {entry: value for entry, value in live["implementations"].items()
                                   if entry not in result["orphan_live"]}
        with catalog_write_lock(live_cases_path(root)):
            atomic_catalog_write(live_cases_path(root), live)


def export(path: Path, root: Path = ROOT) -> int:
    cases, _errors = CatalogRepository(root=root, verify_sources=False).projection()
    path.write_text(json.dumps({"schema": "tfi.test-catalog.v2", "cases": [case.public() for case in cases]},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(cases)


def main(argv: list[str]) -> int:
    result = check()
    for error in result["errors"]:
        print(f"error {error['resource_id']}: {error['error']}")
    for entry, case_id in sorted(result["moved_ids"].items()):
        print(f"moved {case_id}: {entry}")
    for entry, case_id in sorted(result["orphan_ids"].items()):
        print(f"removed {case_id}: {entry}")
    for entry in result["orphan_live"]:
        print(f"removed Live: {entry}")
    for entry in result["untitled"]:
        print(f"untitled {entry}")
    for case_id, ref in sorted(result["stale_points"].items()):
        print(f"stale design point {case_id}: {ref}")
    if "--apply" in argv and (result["moved_ids"] or result["orphan_ids"] or result["orphan_live"]):
        apply(result)
    if "--export" in argv:
        target = Path(argv[argv.index("--export") + 1])
        print(f"exported {export(target)} cases to {target}")
    print(" ".join(f"{key}={len(result[key])}" for key in (
        "errors", "moved_ids", "orphan_ids", "orphan_live", "untitled", "stale_points", "unchecked_points")))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
