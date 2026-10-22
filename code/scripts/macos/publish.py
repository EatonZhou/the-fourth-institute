"""Publish both verified macOS architectures in one prerelease."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def validate(directory, commit):
    assets = []
    for suffix, arch in (("arm64", "aarch64"), ("x64", "x86_64")):
        dmg = directory / f"TheFourthInstitute-macOS-{suffix}.dmg"
        receipt = dmg.with_suffix(".json")
        record = json.loads(receipt.read_text())
        with dmg.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if (record["source_commit"] != commit or record["source_dirty"]
                or record["target"] != "macos/" + arch
                or record["prepared_engineering_validation"] != "passed"
                or not record.get("native_installation_smoke", "").startswith("passed:")
                or record.get("app_signature_verification") != "passed"
                or record.get("dmg_app_signature_verification") != "passed"
                or record.get("signed") is not True
                or record.get("signing_identity") != "ad-hoc"
                or record["dmg_bytes"] != dmg.stat().st_size or record["dmg_sha256"] != actual):
            raise ValueError("DMG does not match its verified source, signature and native smoke results")
        checksum = dmg.with_name(dmg.name + ".sha256")
        if checksum.read_text().strip() != actual + "  " + dmg.name:
            raise ValueError("Checksum file does not match DMG")
        assets.extend([str(dmg), str(checksum), str(receipt)])
    return assets


def publish(directory, commit):
    release = json.loads(Path(__file__).with_name("release.json").read_text())
    if not re.fullmatch(r"macos-preview-\d{8}\.\d+", release["tag"]):
        raise ValueError("Invalid macOS release tag")
    assets = validate(directory, commit)
    subprocess.run(["gh", "release", "create", release["tag"], "--target", commit,
                    "--prerelease", "--latest=false", "--title", release["title"],
                    "--notes-file", str(Path(__file__).with_name("README.md")), *assets], check=True)
    # The shared cleaner independently verifies uploaded assets before deleting
    # older releases. A failed or partial upload must never reach this step.
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "tfi_preview_retention.py"),
                    "--platform", "macos"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-to-github", action="store_true",
                        help="Publish publicly only after an explicit user request; test builds do not authorize publication")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if not args.publish_to_github:
        parser.error("Public release requires --publish-to-github after an explicit user request")
    publish(args.directory, args.commit)
