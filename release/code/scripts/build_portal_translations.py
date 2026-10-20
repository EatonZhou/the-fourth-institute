"""Build/check the browser catalog: the core translation JSON merged with the
catalogs of the features in this package (``management_portal/translations``)."""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "management_portal/static/portal_translations.js"
sys.path.insert(0, str(ROOT))

from management_portal.localization import catalog_paths, merge_catalogs  # noqa: E402


def build() -> str:
    catalog, conflicts = merge_catalogs(catalog_paths())
    if conflicts:
        path, source = conflicts[0]
        raise ValueError(f"{path} translates {source!r} differently from another catalog; keep one entry")
    for source, translations in catalog.items():
        if not isinstance(source, str) or not isinstance(translations, dict) or set(translations) != {"zh-CN", "en"}:
            raise ValueError(f"Invalid language entry: {source}")
        placeholders = sorted(re.findall(r"\{p\d+\}", source))
        for language, text in translations.items():
            if not isinstance(text, str) or not text.strip() or sorted(re.findall(r"\{p\d+\}", text)) != placeholders:
                raise ValueError(f"Invalid translation or placeholders: {source} ({language})")
    # The browser falls back to the source text, so a zh-CN text equal to its source is left out.
    browser_catalog = {
        source: {language: text for language, text in translations.items() if not (language == "zh-CN" and text == source)}
        for source, translations in catalog.items()
    }
    encoded = json.dumps(browser_catalog, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    encoded = encoded.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return "window.__PORTAL_TRANSLATIONS__ = Object.freeze(" + encoded + ");\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = build()
    if args.check:
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Browser catalog is stale; run scripts/build_portal_translations.py")
    else:
        OUTPUT.write_text(rendered, encoding="utf-8")
    print("Portal translation catalog valid")


if __name__ == "__main__":
    main()
