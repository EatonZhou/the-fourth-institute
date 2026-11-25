"""Private, bounded Word/PowerPoint to PDF conversion, after the caller authorizes bytes."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

from management_common.instance_manifest import instance_runtime_root
from management_common.file_lock import try_lock_descriptor
from management_common.process_runtime import run_process
from management_common.file_retention import cache_file, temporary_directory

# Video-bearing decks can be much larger than the static PDF they produce.
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_PDF_BYTES = 128 * 1024 * 1024
CONVERSION_SECONDS = 60
_conversion_slot = threading.Lock()
_PDF_FILTERS = {
    ".doc": "writer_pdf_Export", ".docx": "writer_pdf_Export",
    ".ppt": "impress_pdf_Export", ".pptx": "impress_pdf_Export",
}


def supports_pdf_preview(filename: str) -> bool:
    return Path(filename).suffix.lower() in _PDF_FILTERS


class OfficePreviewError(ValueError):
    """A user-facing preview failure; the original file remains downloadable."""


def libreoffice() -> str:
    executable = (os.environ.get("PORTAL_LIBREOFFICE") or shutil.which("libreoffice")
                  or shutil.which("soffice"))
    if executable:
        return executable

    candidates = [instance_runtime_root() / "tools/libreoffice/soffice"]
    if sys.platform == "darwin":
        bundle = "LibreOffice.app/Contents/MacOS/soffice"
        candidates.extend((Path("/Applications") / bundle, Path.home() / "Applications" / bundle))
    for candidate in candidates:
        executable = shutil.which(str(candidate))
        if executable:
            return executable
    raise OfficePreviewError("服务器尚未安装 LibreOffice，请下载原文件查看。")


def _check_source_size(size: int) -> None:
    if size > MAX_SOURCE_BYTES:
        limit_mib = MAX_SOURCE_BYTES // (1024 * 1024)
        raise OfficePreviewError(f"文档超过 {limit_mib} MiB 预览上限，请下载原文件查看。")


def read_office_document(path: Path) -> bytes:
    with path.open("rb") as stream:
        _check_source_size(os.fstat(stream.fileno()).st_size)
        data = stream.read(MAX_SOURCE_BYTES + 1)
    _check_source_size(len(data))
    return data


def office_pdf(data: bytes, filename: str) -> Path:
    """Never accept a URL or authorize via a cache hit. Inputs are authorized bytes."""
    suffix = Path(filename).suffix.lower()
    if not supports_pdf_preview(filename):
        raise OfficePreviewError("仅支持 DOC、DOCX、PPT 和 PPTX 文件预览。")
    _check_source_size(len(data))
    if not data:
        raise OfficePreviewError("文档为空，请下载原文件检查。")
    # Keep the existing on-disk cache namespace so retained PowerPoint PDFs remain reusable.
    root = instance_runtime_root() / "cache/presentation-preview"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(b"ppt-pdf-v1\0" + suffix.encode() + data).hexdigest()
    cached = root / (key + ".pdf")
    if cached.is_file():
        with cache_file(cached, group='presentation'):
            return cached
    # One process at a time per Web worker; fail fast instead of tying up HTTP threads.
    if not _conversion_slot.acquire(timeout=2):
        raise OfficePreviewError("文档预览繁忙，请稍后刷新重试。")
    lock = None
    try:
        lock = (root / ".convert.lock").open("a+b")
        if not try_lock_descriptor(lock.fileno()):
            raise OfficePreviewError("文档预览繁忙，请稍后刷新重试。")
        if cached.is_file():
            with cache_file(cached, group='presentation'):
                return cached
        # A retained PDF is usable without starting or locating the converter.
        # Resolve the dependency only after both cache checks miss.
        executable = libreoffice()
        with temporary_directory(prefix="convert-", dir=root) as directory:
            work = Path(directory)
            source = work / ("document" + suffix)
            source.write_bytes(data)
            profile = work / "profile"
            (profile / "user").mkdir(parents=True)
            (profile / "user/registrymodifications.xcu").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
                '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
                '<item oor:path="/org.openoffice.Office.Common/Load">'
                '<prop oor:name="UpdateMode" oor:op="fuse"><value>0</value></prop></item>'
                '</oor:items>', encoding="utf-8")
            try:
                result = run_process([
                    executable, "-env:UserInstallation=" + profile.as_uri(),
                    "--headless", "--nologo", "--nodefault", "--norestore",
                    "--convert-to", "pdf:" + _PDF_FILTERS[suffix], "--outdir", str(work), str(source),
                ], cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=CONVERSION_SECONDS)
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                raise OfficePreviewError("文档转换超时，请下载原文件查看。") from exc
            except OSError as exc:
                raise OfficePreviewError("文档转换服务不可用，请下载原文件查看。") from exc
            output = work / "document.pdf"
            if result.returncode or not output.is_file():
                raise OfficePreviewError("无法转换此文档；文件可能损坏或已加密，请下载原文件查看。")
            if output.stat().st_size > MAX_PDF_BYTES:
                raise OfficePreviewError("转换后的文档过大，请下载原文件查看。")
            with output.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise OfficePreviewError("文档转换失败，请下载原文件查看。")
            output.chmod(0o600)
            with cache_file(cached, group='presentation'):
                output.replace(cached)
        return cached
    finally:
        if lock is not None:
            lock.close()
        _conversion_slot.release()
