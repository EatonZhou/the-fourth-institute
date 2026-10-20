#!/usr/bin/env python3
"""Shorten public example paths and rebuild their reference/integrity records.

This is a release-data transformation, never an installed-instance migration.
The input must be an inventoried example seed. All work happens in a new output
directory; resource identities, database rows and recorded research are retained.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from prepare_install import digest, inventory

OLD_MATERIAL = "release-runtime-v1/workspace_13209cb6666b/"
TEXT_EXTENSIONS = {".json", ".yaml", ".yml", ".md", ".js", ".html", ".css", ".py", ".txt"}
SQL_STRING = re.compile(r"\bE'(?:\\.|''|[^'])*'|'(?:[^']|'')*'")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def replace(text, mappings):
    for old, new in sorted(mappings.items(), key=lambda item: -len(item[0])):
        text = text.replace(old, new)
    return text


def path_plan(seed):
    mappings = {
        OLD_MATERIAL + "architecture-evidence/manifest.json": "architecture/manifest.json",
        OLD_MATERIAL + "architecture-evidence/current/architecture/": "architecture/model/",
        OLD_MATERIAL + "architecture-evidence/current/": "architecture/model/",
        OLD_MATERIAL + "deep-architecture/current/": "architecture/explore/",
    }
    for path in seed.rglob("isa-catalog-attachment-*.json"):
        suffix = path.as_posix().split(OLD_MATERIAL, 1)
        if len(suffix) == 2:
            mappings[OLD_MATERIAL + suffix[1]] = "architecture/catalog/isa.json"
    # Clip identity remains in catalog.id; the file is located by offlineClip /
    # framesFile, not by reconstructing that identity. JSON and Worker JS use
    # the same stem because the published loader maps .json to .js.
    for path in seed.rglob("*"):
        if path.parent.name != "clips" or path.suffix not in {".json", ".js"}:
            continue
        stem = path.name.split(".", 1)[0] if ".frames." in path.name else path.stem
        # Preserve decimal points inside names (e.g. g0.35).
        if ".frames." in path.name:
            stem = path.name.rsplit(".frames.", 1)[0]
        if len(stem) <= 24 or stem.startswith("c-"):
            continue
        short = "c-" + hashlib.sha256(stem.encode()).hexdigest()[:16]
        for suffix in (".json", ".frames.json", ".js", ".frames.js"):
            mappings["clips/" + stem + suffix] = "clips/" + short + suffix
    return mappings


def update_descriptors(value, seed, workspace_roots, repo_root):
    """Refresh file descriptors only when their declared local target exists."""
    if isinstance(value, list):
        return [update_descriptors(v, seed, workspace_roots, repo_root) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: update_descriptors(v, seed, workspace_roots, repo_root) for k, v in value.items()}
    target = None
    address = result.get("address")
    if isinstance(address, str) and "/file/" in address:
        workspace, relative = address.split("/file/", 1)
        if workspace in workspace_roots:
            target = workspace_roots[workspace] / relative
            if not target.is_file():
                raise ValueError(f"Missing relocated resource: {address}")
    if result.get("base") == "repo" and isinstance(result.get("path"), str):
        target = repo_root / result["path"]
    if target is not None and target.is_file() and "sha256" in result:
        result["sha256"] = digest(target)
        for key in ("size", "bytes"):
            if key in result:
                result[key] = target.stat().st_size
    return result


def sql_strings(text, transform, mappings):
    def one(match):
        if match.group().startswith("E'"):
            # All relocations are literal ASCII path fragments, without SQL
            # quotes or backslashes. Preserve the exporter's escaping exactly.
            # Application build records use normal literals in this seed.
            if '"artifact_hash"' in match.group():
                raise ValueError("Escaped application build requires SQL-aware import")
            return replace(match.group(), mappings)
        value = match.group()[1:-1].replace("''", "'")
        updated = transform(value)
        return match.group() if updated == value else "'" + updated.replace("'", "''") + "'"
    return SQL_STRING.sub(one, text)


def compact(seed, output):
    specification = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    before = [r for r in inventory(seed) if r["path"] != "manifest.json"]
    if before != sorted(specification["inventory"], key=lambda r: r["path"]):
        raise ValueError("Example seed differs from its inventory")
    if output.exists():
        raise ValueError("Output must not exist")
    mappings = path_plan(seed)
    targets = [replace(row["path"], mappings) for row in before]
    if len(targets) != len(set(targets)):
        raise ValueError("Short path collision")
    shutil.copytree(seed, output)
    for path in output.rglob("*"):
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o200)
    moved = {}
    for row, target in zip(before, targets):
        if target != row["path"]:
            source, destination = output / row["path"], output / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            moved[row["path"]] = target
    for path in output.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_EXTENSIONS or path.name == "manifest.json":
            continue
        original = path.read_text(encoding="utf-8")
        updated = replace(original, mappings)
        if original != updated:
            path.write_text(updated, encoding="utf-8")
    # manifest.json can be both the seed inventory and an architecture root.
    for path in output.rglob("manifest.json"):
        if path != output / "manifest.json":
            original = path.read_text(encoding="utf-8")
            path.write_text(replace(original, mappings), encoding="utf-8")
    architecture = list(output.glob("data/share_root/Agents/**/workspace/architecture/manifest.json"))
    if len(architecture) != 1:
        raise ValueError("Expected one current architecture root")
    roots = {"workspace_04ad72d24263": architecture[0].parent.parent}
    lenia = list(output.glob("data/share_root/Agents/**/workspace/lenia-core"))
    if len(lenia) != 1:
        raise ValueError("Expected one Lenia source root")
    # Descriptor graphs point to bytes that may themselves contain descriptors.
    # Settle bottom-up; fail rather than publish an unresolved hash cycle.
    json_files = [p for p in output.rglob("*.json") if p != output / "manifest.json" and p.name != "artifact.json"]
    for _ in range(20):
        changes = 0
        for path in json_files:
            value = json.loads(path.read_text(encoding="utf-8"))
            updated = update_descriptors(value, output, roots, lenia[0])
            if value != updated:
                save_json(path, updated)
                changes += 1
        if not changes:
            break
    else:
        raise ValueError("File descriptor hashes did not converge")
    builds = {}
    artifact_root = output / "data/staging_root/shared/projection_applications"
    for directory in sorted(artifact_root.iterdir()):
        index_path = directory / "artifact.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        updated = {"entries": index["entries"], "files": {}}
        for name, info in index["files"].items():
            relative = replace(name, mappings)
            asset = directory / "files" / relative
            updated["files"][relative] = {**info, "sha256": digest(asset), "size": asset.stat().st_size}
        raw = canonical(updated)
        hash_ = hashlib.sha256(raw).hexdigest()
        index_path.write_bytes(raw)
        if hash_ != directory.name:
            builds[directory.name] = {"artifact_hash": hash_, **updated}
            directory.rename(directory.with_name(hash_))
    sql_path = output / specification["engineering"]["database"]["path"]
    original_sql = sql_path.read_text(encoding="utf-8")
    def transform(value):
        try:
            obj = json.loads(value)
        except (ValueError, TypeError):
            return replace(value, mappings)
        def walk(item):
            if isinstance(item, list):
                return [walk(v) for v in item]
            if isinstance(item, dict):
                if item.get("artifact_hash") in builds:
                    item = {**item, **builds[item["artifact_hash"]]}
                return {k: walk(v) for k, v in item.items()}
            if isinstance(item, str):
                return replace(item, mappings)
            return item
        changed = walk(obj)
        return json.dumps(changed, ensure_ascii=False) if changed != obj else value
    sql_path.write_text(sql_strings(original_sql, transform, mappings), encoding="utf-8")
    specification = json.loads(replace(json.dumps(specification, ensure_ascii=False), mappings))
    # The engineering declaration describes the copied current material too.
    specification["engineering"] = update_descriptors(specification["engineering"], output, roots, lenia[0])
    specification["engineering"]["database"]["sha256"] = digest(sql_path)
    # Artifact hashes in export audit metadata refer to the relocated builds.
    specification = json.loads(replace(json.dumps(specification, ensure_ascii=False),
                                       {old: new["artifact_hash"] for old, new in builds.items()}))
    for path in sorted(output.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()) and OLD_MATERIAL.split("/")[0] in path.parts:
            path.rmdir()
    specification["inventory"] = [r for r in inventory(output) if r["path"] != "manifest.json"]
    save_json(output / "manifest.json", specification)
    return {"moved": moved, "artifacts": {k: v["artifact_hash"] for k, v in builds.items()},
            "files": len(before), "max_path_before": max(len(r["path"]) for r in before),
            "max_path_after": max(len(r["path"]) for r in specification["inventory"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=REPO / "instance/example_instance/seed")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = compact(args.seed, args.output)
    save_json(args.report, result)
    print(json.dumps({k: v for k, v in result.items() if k != "moved"}))
