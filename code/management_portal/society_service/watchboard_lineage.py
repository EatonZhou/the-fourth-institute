"""Read-only Watchboard projection and installation plan over a Lineage definition.

The browser applies the plan with UpdateWorkspaceFile and ApplyLineageChanges.
No Watchboard-specific mutation or runtime is owned here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

def root(record):
    from .resident_record_domain import _resident_owned_workspace_path
    return _resident_owned_workspace_path(record) / "watchboard"


def prefix(record):
    return "watchboard." + str(record["resident_id"]) + "."


def definition(record):
    from .lineage_domain import _lineage_catalog_store
    from . import resident_lineage
    lineage_id = resident_lineage.lineage_id(record)
    if not lineage_id:
        return {"revision": 0, "program": {}}
    cache_key = "watchboard-definition:" + lineage_id
    cached = resident_lineage.cached_access_projection(cache_key)
    if cached is not None:
        return cached
    graph = _lineage_catalog_store().get_state(lineage_id)[0]
    resident_lineage.remember_access_projection(cache_key, graph)
    return graph


def components(record, graph=None):
    program = (graph if graph is not None else definition(record)).get("program") or {}
    return [item for item in program.get("components", [])
            if str(item.get("client_key") or "").startswith(prefix(record))]


def installed(record):
    return any(item.get("client_key") == prefix(record) + "view" for item in components(record))


def component_id(record, kind, key):
    token = hashlib.sha256((prefix(record) + key).encode()).hexdigest()[:20]
    return kind + "_" + token


def command_ids(record, command):
    key = "command." + hashlib.sha256(command.encode()).hexdigest()[:20]
    return {"event_source_id": component_id(record, "event_source", key),
            "effect_id": component_id(record, "effect", key), "key": key}


def installation_plan(record, graph=None):
    from . import resident_watchboard_domain as views

    graph = definition(record) if graph is None else graph
    current = components(record, graph)
    present = {item["id"] for item in current}
    program = graph.get("program") or {}
    remove = [{"op": "delete", "kind": "wire", "id": item["id"]} for item in program.get("wires", [])
              if item.get("source") in present or item.get("target") in present]
    remove += [{"op": "delete", "kind": "component", "id": item["id"]} for item in current]
    from . import resident_lineage
    plan = {"lineage_id": resident_lineage.lineage_id(record), "revision": graph.get("revision", 0),
            "files": [], "install": [], "remove": remove}
    # Removing an installed definition must work even when its files are damaged.
    if any(item.get("client_key") == prefix(record) + "view" for item in current):
        return plan
    script_root = root(record)
    manifest_path = script_root / "agent.yaml"
    manifest, error = views._read_watchboard_agent_manifest(record) if manifest_path.exists() else ({}, "")
    if error:
        raise ValueError(error)
    if not manifest:
        manifest = views._default_watchboard_agent_manifest(record)
        manifest["root"] = str(script_root)
    defaults = {
        "agent.yaml": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        "watchboard.yaml": json.dumps(views._default_watchboard_view(record), ensure_ascii=False, indent=2) + "\n",
        "state/status.json": "{}\n",
        "README.md": views._watchboard_readme_text(record),
    }
    if manifest["commands"]:
        defaults["src/main.py"] = views._default_watchboard_main_py()
        defaults["config/config.yaml"] = "{}\n"
    files = [{"address": record["owned_workspace_id"] + "/file/watchboard/" + path, "content": content}
             for path, content in defaults.items() if not (script_root / path).exists()]
    view_id = component_id(record, "event_source", "view")
    wanted = [{"id": view_id, "kind": "event_source", "client_key": prefix(record) + "view",
               "title": "Watchboard · " + str(record.get("title") or record["resident_id"]),
               "config": {"event_type": "manual.triggered"}}]
    wires = []
    runner = Path(__file__).resolve().parents[2] / "scripts" / "run_watchboard_command.py"
    for command, spec in manifest["commands"].items():
        if not isinstance(spec, dict):
            raise ValueError("agent.yaml command definitions must be objects")
        ids = command_ids(record, command)
        timeout = spec.get("timeout_seconds", manifest.get("default_timeout_seconds"))
        command_input = {"entrypoint": manifest["entrypoint"], "spec": {
            key: spec[key] for key in ("map_to", "args", "parameters") if key in spec}}
        arguments = json.dumps(command_input, ensure_ascii=False)[:-1] + ', "prompt":${event.payload.prompt}}'
        script = "RunScript " + " ".join([
            "path:=" + json.dumps(str(runner)),
            "argv:=" + json.dumps([str(script_root), command]),
            "cwd:=" + json.dumps(str(script_root)),
            "arguments:=" + arguments,
            *(["timeout_seconds:=" + json.dumps(timeout)] if timeout is not None else []),
            "exclusive_key:=" + json.dumps(prefix(record) + "run"),
        ])
        wanted.extend([
            {"id": ids["event_source_id"], "kind": "event_source", "client_key": prefix(record) + ids["key"] + ".trigger",
             "title": "Watchboard · " + command, "config": {"event_type": "manual.triggered"}},
            {"id": ids["effect_id"], "kind": "effect", "client_key": prefix(record) + ids["key"] + ".effect",
             "title": "Watchboard · " + command, "config": {"script": script}},
        ])
        wires.append({"id": component_id(record, "wire", ids["key"]), "kind": "route",
                      "source": ids["event_source_id"], "target": ids["effect_id"]})
    present_wires = {item["id"] for item in program.get("wires", [])}
    install = [{"op": "create", "kind": "component", "value": item} for item in wanted if item["id"] not in present]
    install += [{"op": "create", "kind": "wire", "value": item} for item in wires if item["id"] not in present_wires]
    return {**plan, "files": files, "install": install}
