"""Poppler command-line tools over a PDF file: text and document properties."""

from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil
import subprocess

from management_common import process_runtime


def _poppler_command(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(
            f"{name} is not installed; install Poppler for this platform and add its bin directory to PATH"
        )
    return executable


def _pdf_document_properties(pdf_path: Path) -> dict[str, str]:
    try:
        proc = process_runtime.run_process(
            [_poppler_command("pdfinfo"), "-enc", "UTF-8", str(pdf_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("pdfinfo metadata unavailable: %s", exc)
        return {}
    if proc.returncode != 0:
        return {}
    result: dict[str, str] = {}
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and value.strip():
            result[key.strip().casefold()] = value.strip()
    return result


def _extract_pdf_text_file(pdf_path: Path, *, cache_path: Path | None = None) -> str:
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.is_file() and cache_path.stat().st_size > 1000:
            return cache_path.read_text(encoding="utf-8", errors="replace")
    proc = process_runtime.run_process(
        [_poppler_command("pdftotext"), "-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=360,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"pdftotext failed: {stderr}")
    text = proc.stdout.decode("utf-8", errors="replace")
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if cache_path:
        cache_path.write_text(text, encoding="utf-8")
    return text
