"""Installer-owned boundary for the exporter-owned feature defaults helper."""
from pathlib import Path
import json
import sys


def apply(api, state, manifest, *, mode="fresh", existing_feature_ids=(), freeze_evidence=None):
    from scripts.tfi_instance_layout import code_root
    if not manifest.get("feature_resolution"):
        return {"status": "legacy_not_declared", "features": [], "work_dispatched": False}
    target = state["target"]
    root = Path(target["install_root"])
    previous = state.get("feature_initialization", {})
    if previous.get("manifest_sha256") == state["manifest_sha256"]:
        if previous.get("status") == "applied":
            return previous["result"]
        raise ValueError("Feature initialization was interrupted; recover an upgrade or archive and rebuild the owned fresh instance")
    evidence = None
    if mode == "fresh":
        if freeze_evidence is None:
            if target.get("supervisor_resident_id"):
                raise ValueError("Supervised installation requires --freeze-evidence")
            output = api.run_checked(
                [api.runtime_python(target), str(code_root(target) / "scripts/tfi_release_initialization.py"),
                 str(root), "--verify-frozen"], target=target, cwd=code_root(target))
            evidence = json.loads(output)
        else:
            evidence = api.read_json(freeze_evidence)
        expected = {"run_id": target["run_id"], "supervisor_resident_id": target["supervisor_resident_id"],
                    "manifest_sha256": state["manifest_sha256"]}
        if (evidence.get("passed") is not True or any(evidence.get(k) != v for k, v in expected.items())
                or any(evidence.get("violations", {}).values()) or "database" not in state.get("completed", [])):
            raise ValueError("Freeze evidence does not match the prepared target and immutable manifest")
    elif mode != "upgrade":
        raise ValueError("Unknown feature initialization mode")
    controller = api.service_controller()
    for component in api.COMPONENTS[1:]:
        status = controller.status(api.unit_name(target, component))
        if status.get("pid") or status["active"] not in {"inactive", "failed"}:
            raise ValueError("Feature initialization requires all application services stopped")
    request = {"mode": mode, "existing_feature_ids": list(existing_feature_ids),
               "manifest_sha256": state["manifest_sha256"]}
    api.write_json(root / "runtime/feature-initialization-request.json", request)
    if evidence is not None:
        api.write_json(root / "runtime/history-freeze-verification.json", evidence)
    state["feature_initialization"] = {"status": "applying", "manifest_sha256": state["manifest_sha256"]}
    api.save_stage(state, "feature_initialization", "initializing")
    output = api.run_checked([api.runtime_python(target), str(code_root(target) / "scripts/tfi_release_initialization.py"),
                              str(root)], target=target, cwd=code_root(target))
    result = json.loads(output)
    if result.get("work_dispatched") is not False:
        raise ValueError("Initialization must not dispatch business work")
    state["feature_initialization"].update(status="applied", result=result)
    api.write_json(root / "runtime/feature-initialization.json", result)
    api.save_stage(state, "feature_initialization", "initialized")
    return result


def initialize_target(root):
    """Executed only with the validated target environment, in a fresh process."""
    import psycopg
    from scripts import tfi_release_install as api
    from management_portal.distribution.initialization import initialize

    state = api.read_json(root / api.STATE_FILE)
    target = state["target"]
    request = api.read_json(root / "runtime/feature-initialization-request.json")
    manifest_path = root / "runtime/engineering-manifest.json"
    if (Path(target["install_root"]).resolve() != root
            or api.digest(manifest_path) != request["manifest_sha256"]
            or state["manifest_sha256"] != request["manifest_sha256"]):
        raise ValueError("Target initialization identity changed")
    with psycopg.connect(api.clean_environment(target)["PORTAL_DATABASE_URL"], autocommit=True) as connection:
        with connection.transaction():
            result = initialize(api.read_json(manifest_path), connection, target["data_roots"],
                                mode=request["mode"], existing_feature_ids=request["existing_feature_ids"],
                                site_config_path=root / "config/site.json")
    return result


def verify_target(root):
    import psycopg
    from scripts import tfi_release_install as api
    from management_portal.distribution.history import verify_frozen
    state = api.owned_state(root)
    target = state["target"]
    if "database" not in state.get("completed", []):
        raise ValueError("Database import has not completed")
    for component in api.COMPONENTS[1:]:
        status = api.service_controller().status(api.unit_name(target, component))
        if status["pid"] or status["active"] not in {"inactive", "failed"}:
            raise ValueError("Stop application services before checking frozen history")
    with psycopg.connect(api.clean_environment(target)["PORTAL_DATABASE_URL"]) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        result = verify_frozen(connection)
    return {**result, "run_id": target["run_id"], "supervisor_resident_id": target["supervisor_resident_id"],
            "manifest_sha256": state["manifest_sha256"], "verification": "target-database-read-only"}


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    operation = verify_target if sys.argv[2:] == ["--verify-frozen"] else initialize_target
    print(json.dumps(operation(Path(sys.argv[1]).resolve()), ensure_ascii=False))
