#!/usr/bin/env python3
"""Check tracked working-tree names and text for distribution naming violations.

Plain output contains findings only (exit 0 clean, 1 findings, 2 invalid input).
The policy file is parsed as policy, not scanned as product source. Exceptions
are exact source lines, or explicitly enumerated third-party directories. This
tool does not export files, migrate data, or inspect untracked local secrets.
Brand/user terms must be supplied as an external identity scope. Compressed
text fixtures are inspected after decompression.
"""
from __future__ import annotations

import argparse
import gzip
from dataclasses import asdict, dataclass
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY = "distribution/scan_rules.yaml"
EXCEPTION_KINDS = {"compat_read", "scanner_rule", "legacy_fixture", "vendor"}
PRIVATE_NETWORKS = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


@dataclass(frozen=True)
class Finding:
    path: str
    line: int  # zero denotes the path itself
    column: int
    rule: str
    match: str


def load_policy(root: Path, relative: str = POLICY) -> dict:
    path = root / relative
    if path.is_symlink():
        raise ValueError("naming policy must be a regular repository file")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"schema", "rules", "exceptions", "deferred"} or data["schema"] != "tfi.distribution-scan-rules.v1":
        raise ValueError("invalid naming policy schema")
    if not isinstance(data["rules"], dict) or not {"laboratory-path", "private-address"} <= set(data["rules"]) or set(data["rules"]) - {"legacy-name", "laboratory-path", "private-address"}:
        raise ValueError("policy must declare all naming rules")
    for pattern in data["rules"].values():
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("rule patterns must be non-empty strings")
        re.compile(pattern)
    if not all(isinstance(data[k], list) for k in ("exceptions", "deferred")):
        raise ValueError("exceptions and deferred must be arrays")
    for entry in data["exceptions"] + data["deferred"]:
        deferred = entry in data["deferred"]
        if not isinstance(entry, dict) or entry.get("kind") not in ( {"deferred"} if deferred else EXCEPTION_KINDS):
            raise ValueError("invalid exception kind")
        required = {"kind", "path", "reason"}
        if entry["kind"] != "vendor":
            required |= {"rule", "pattern"}
        if deferred:
            required |= {"match_pattern", "basis"}
        if set(entry) != required or not all(isinstance(v, str) and v.strip() for v in entry.values()):
            raise ValueError("exceptions require exact paths, source lines and a reason")
        rel = PurePosixPath(entry["path"])
        if rel.is_absolute() or ".." in rel.parts or any(c in entry["path"] for c in "*?[]\\"):
            raise ValueError("exception paths must be literal repository-relative paths")
        if entry["kind"] != "vendor":
            pattern = entry["pattern"]
            if entry["rule"] not in data["rules"] or "\n" in pattern or "\r" in pattern or not pattern.startswith("^") or not pattern.endswith("$") or re.fullmatch(pattern, "") is not None:
                raise ValueError("exception must identify a rule and anchored non-empty source line")
            if (root / rel).is_dir():
                raise ValueError("only vendor exceptions may name directories")
            if deferred:
                re.compile(entry["match_pattern"])
        elif not (entry["path"].startswith("management_portal/static/vendor/") or entry["path"].startswith("management_portal/static/projection_libraries/")):
            raise ValueError("vendor exceptions must name an explicit bundled library")
    return data


def identity_policy(policy, scope):
    """Explicit external audit scope, never a baked-in site's names or brand."""
    if not isinstance(scope, dict): raise ValueError("identity scope must be an object")
    users, brands = scope.get('source_usernames', []), scope.get('forbidden_brands', [])
    if not isinstance(users,list) or not isinstance(brands,list) or not users+brands:
        raise ValueError("identity scope requires source_usernames or forbidden_brands")
    if any(not isinstance(x,str) or not x.strip() for x in users+brands):
        raise ValueError("identity terms must be nonempty strings")
    rules = dict(policy['rules'])
    if brands: rules['legacy-name'] = '(?i)(?:'+'|'.join(re.escape(x) for x in brands)+')'
    if users: rules['source-user'] = r'(?<![\w])(?:'+'|'.join(re.escape(x) for x in users)+r')(?![\w])'
    return {**policy, 'rules': rules}


def matches(text: str, rules: dict):
    for rule, pattern in rules.items():
        for match in re.finditer(pattern, text):
            if rule == "private-address":
                try:
                    address = ipaddress.ip_interface(match.group())
                except ValueError:
                    continue
                if not any(address.ip in net for net in PRIVATE_NETWORKS):
                    continue
                # RFC1918 definitions are portable protocol constants, not site
                # addresses. Subnets within them remain findings.
                if "/" in match.group() and address.network in PRIVATE_NETWORKS and address.ip == address.network.network_address:
                    continue
            yield rule, match.start() + 1, match.group()


def scan(root: Path, paths: list[str], policy: dict) -> tuple[list[Finding], int, list[dict]]:
    findings = []
    allowed = 0
    deferred_findings = []
    exceptions = policy["exceptions"]
    for relative in sorted(set(paths)):
        path = root / relative
        if relative == POLICY or not path.exists() and not path.is_symlink():
            continue
        vendor = any(e["kind"] == "vendor" and (relative == e["path"] or relative.startswith(e["path"].rstrip("/") + "/")) for e in exceptions)
        if vendor:
            allowed += 1
            continue
        lines = [(0, relative)]
        if path.is_symlink():
            lines.append((1, str(path.readlink())))  # never follow external content
        elif path.is_file():
            raw = path.read_bytes()
            if path.name.endswith(".gz"):
                raw = gzip.decompress(raw)
            if b"\0" not in raw:
                lines.extend(enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1))
        for line_number, source in lines:
            for rule, column, value in matches(source, policy["rules"]):
                finding = Finding(relative, line_number, column, rule, value)
                if line_number and any(e["kind"] != "vendor" and e["path"] == relative and e["rule"] == rule and re.fullmatch(e["pattern"], source) for e in exceptions):
                    allowed += 1
                else:
                    deferred = next((e for e in policy["deferred"] if e["path"] == relative and e["rule"] == rule and re.fullmatch(e["pattern"], source) and any(m.start() <= column - 1 and m.end() >= column - 1 + len(value) for m in re.finditer(e["match_pattern"], source))), None)
                    if deferred:
                        deferred_findings.append({**asdict(finding), "reason": deferred["reason"], "basis": deferred["basis"]})
                    else:
                        findings.append(finding)
    return findings, allowed, deferred_findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--identity-scope", type=Path, required=True, help="External JSON audit scope: source_usernames and forbidden_brands; no implicit clean identity result")
    parser.add_argument("--json", action="store_true", help="emit structured evidence, including successful scans")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        git_root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, stderr=subprocess.PIPE).decode().strip()).resolve()
        if git_root != root:
            raise ValueError("--root must be the Git work-tree root")
        paths = subprocess.check_output(["git", "ls-files", "--cached", "-z"], cwd=root).decode("utf-8").split("\0")
        scope = json.loads(args.identity_scope.read_text(encoding="utf-8"))
        findings, allowed, deferred = scan(root, [p for p in paths if p], identity_policy(load_policy(root), scope))
    except (ValueError, OSError, EOFError, re.error, yaml.YAMLError, subprocess.CalledProcessError) as error:
        print(f"naming check: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"schema": "tfi.distribution-naming-report.v1", "findings": [asdict(f) for f in findings], "allowed": allowed, "deferred": deferred, "identity_scope_configured": True}, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(f"{f.path}:{f.line}:{f.column}: {f.rule}: {json.dumps(f.match, ensure_ascii=False)}")
        for f in deferred:
            print(f"deferred: {f['path']}:{f['line']}: {f['match']}: {f['basis']}", file=sys.stderr)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
