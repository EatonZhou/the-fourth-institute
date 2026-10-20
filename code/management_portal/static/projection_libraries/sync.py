"""Maintainer-only vendor sync. Uses npm's cache, verifies the official tarball.

Run explicitly when reviewing library versions; never imported by the builder
or HTTP runtime. Generated manifest.json and vendor files must be reviewed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parent
PACKAGES = {
    "d3": ("d3", "7.9.0", "dist/d3.min.js", ["dist/d3.js"], "LICENSE", "ISC"),
    "dagre": ("@dagrejs/dagre", "1.1.5", "dist/dagre.min.js", ["dist/dagre.js"], "LICENSE", "MIT"),
    "cytoscape": ("cytoscape", "3.33.1", "dist/cytoscape.min.js", ["dist/cytoscape.esm.mjs"], "LICENSE", "MIT"),
    "marked": ("marked", "18.1.1", "lib/marked.umd.js", ["lib/marked.esm.js"], "LICENSE", "MIT"),
    "dompurify": ("dompurify", "3.4.17", "dist/purify.min.js", ["dist/purify.es.mjs", "LICENSE-MPL"], "LICENSE", "(MPL-2.0 OR Apache-2.0)"),
}


def npm_json(*arguments):
    result = subprocess.run(["npm", *arguments, "--json", "--registry=https://registry.npmjs.org", "--prefer-offline"],
                            check=True, capture_output=True, text=True, timeout=90)
    return json.loads(result.stdout)


def package_files(package, version, files):
    metadata = npm_json("view", f"{package}@{version}")
    if metadata["version"] != version:
        raise ValueError("Version mismatch")
    with tempfile.TemporaryDirectory(prefix="projection-library-") as temporary:
        packed = npm_json("pack", f"{package}@{version}", "--ignore-scripts", "--pack-destination", temporary)[0]
        content = (Path(temporary) / packed["filename"]).read_bytes()
    algorithm, digest = metadata["dist"]["integrity"].split("-", 1)
    if base64.b64encode(hashlib.new(algorithm, content).digest()).decode() != digest:
        raise ValueError(f"Integrity mismatch for {package}@{version}")
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        result = {}
        for name in files:
            member = archive.getmember("package/" + name)
            if not member.isfile():
                raise ValueError("Only regular package files may be vendored")
            result[name] = archive.extractfile(member).read()
    return result, {
        "url": f"https://registry.npmjs.org/{package.replace('/', '%2f')}/{version}",
        "tarball": metadata["dist"]["tarball"],
        "integrity": metadata["dist"]["integrity"],
        "sha256": hashlib.sha256(content).hexdigest(),
        "repository": metadata.get("repository", {}).get("url", ""),
    }


def record(path):
    content = path.read_bytes()
    mime = "application/javascript" if path.suffix in {".js", ".mjs"} else "application/json" if path.suffix == ".json" else "text/plain"
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content), "content_type": mime}


def main():
    libraries = {}
    for name, (package, version, entry, sources, license_file, license_name) in PACKAGES.items():
        directory = ROOT / name / version
        directory.mkdir(parents=True, exist_ok=True)
        content, origin = package_files(package, version, [entry, *sources, license_file, "package.json"])
        files = []
        for source, data in content.items():
            path = directory / Path(source).name
            path.write_bytes(data)
            files.append(record(path))
        licenses = [{"path": f"{name}/{version}/{license_file}", "spdx": license_name, "package": package}]
        if name == "dompurify":
            licenses.append({"path": f"{name}/{version}/LICENSE-MPL", "spdx": "MPL-2.0", "package": package})
        dependencies = []
        if name == "dagre":
            # The browser distribution bundles graphlib; preserve its license too.
            dep_files, dep_origin = package_files("@dagrejs/graphlib", "2.2.4", ["LICENSE"])
            path = directory / "graphlib-LICENSE"
            path.write_bytes(dep_files["LICENSE"])
            files.append(record(path))
            licenses.append({"path": path.relative_to(ROOT).as_posix(), "spdx": "MIT", "package": "@dagrejs/graphlib"})
            dependencies.append({"package": "@dagrejs/graphlib", "version": "2.2.4", "origin": dep_origin})
        libraries[name] = {"package": package, "version": version, "entry": f"{name}/{version}/{Path(entry).name}",
                           "files": files, "licenses": licenses, "origin": origin, "dependencies": dependencies}
        print(f"{name}@{version}: {sum(item['size'] for item in files)} bytes")
    libraries["portal-graph"] = {
        "package": "portal-graph", "version": "1.0.0", "entry": "portal-graph/1.0.0/geometry.js",
        "files": [record(ROOT / "portal-graph/1.0.0" / name) for name in ["geometry.js", "LICENSE.txt"]],
        "licenses": [{"path": "portal-graph/1.0.0/LICENSE.txt", "spdx": "LicenseRef-Portal", "package": "portal-graph"}],
        "origin": {"source": "management_portal/static/lineage_graph.js"}, "dependencies": [],
    }
    (ROOT / "manifest.json").write_text(json.dumps({"schema": "tfi.projection-libraries.v1", "libraries": libraries},
                                                 indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
