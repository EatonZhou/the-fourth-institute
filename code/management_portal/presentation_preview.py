"""Private, bounded PPT/PPTX to PDF conversion, after the caller authorizes bytes."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from management_common.instance_paths import instance_root
from management_common.file_lock import try_lock_descriptor
from management_common.process_runtime import run_process

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PDF_BYTES = 128 * 1024 * 1024
CACHE_BYTES = 512 * 1024 * 1024
CONVERSION_SECONDS = 60
_conversion_slot = threading.Lock()


class PresentationPreviewError(ValueError):
    """A user-facing preview failure; the original file remains downloadable."""


def libreoffice() -> str:
    executable = (os.environ.get("PORTAL_LIBREOFFICE") or shutil.which("libreoffice")
                  or shutil.which("soffice"))
    if not executable:
        local = instance_root() / "tools/libreoffice/soffice"
        executable = str(local) if local.is_file() else ""
    if not executable:
        raise PresentationPreviewError("服务器尚未安装 LibreOffice，请下载原文件查看。")
    return executable


def read_presentation(path: Path) -> bytes:
    with path.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > MAX_SOURCE_BYTES:
            raise PresentationPreviewError("演示文稿超过 64 MiB 预览上限，请下载原文件查看。")
        data = stream.read(MAX_SOURCE_BYTES + 1)
    return data


def _prune_cache(root: Path, keep: Path) -> None:
    files = sorted(root.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
    total = 0
    for path in files:
        total += path.stat().st_size
        if path != keep and (total > CACHE_BYTES or time.time() - path.stat().st_mtime > 7 * 86400):
            path.unlink(missing_ok=True)


def presentation_pdf(data: bytes, filename: str) -> Path:
    """Never accept a URL or authorize via a cache hit. Inputs are authorized bytes."""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".ppt", ".pptx"}:
        raise PresentationPreviewError("仅支持 PPT 和 PPTX 演示文稿预览。")
    if len(data) > MAX_SOURCE_BYTES:
        raise PresentationPreviewError("演示文稿超过 64 MiB 预览上限，请下载原文件查看。")
    if not data:
        raise PresentationPreviewError("演示文稿为空，请下载原文件检查。")
    executable = libreoffice()
    root = instance_root() / "cache/presentation-preview"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(b"ppt-pdf-v1\0" + suffix.encode() + data).hexdigest()
    cached = root / (key + ".pdf")
    if cached.is_file():
        return cached
    # One process at a time per Web worker; fail fast instead of tying up HTTP threads.
    if not _conversion_slot.acquire(timeout=2):
        raise PresentationPreviewError("演示文稿预览繁忙，请稍后刷新重试。")
    lock = None
    try:
        lock = (root / ".convert.lock").open("a+b")
        if not try_lock_descriptor(lock.fileno()):
            raise PresentationPreviewError("演示文稿预览繁忙，请稍后刷新重试。")
        if cached.is_file():
            return cached
        with tempfile.TemporaryDirectory(prefix="convert-", dir=root) as directory:
            work = Path(directory)
            source = work / ("slides" + suffix)
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
                    "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(work), str(source),
                ], cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=CONVERSION_SECONDS)
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                raise PresentationPreviewError("演示文稿转换超时，请下载原文件查看。") from exc
            except OSError as exc:
                raise PresentationPreviewError("演示文稿转换服务不可用，请下载原文件查看。") from exc
            output = work / "slides.pdf"
            if result.returncode or not output.is_file():
                raise PresentationPreviewError("无法转换此演示文稿；文件可能损坏或已加密，请下载原文件查看。")
            if output.stat().st_size > MAX_PDF_BYTES:
                raise PresentationPreviewError("转换后的文稿过大，请下载原文件查看。")
            with output.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise PresentationPreviewError("演示文稿转换失败，请下载原文件查看。")
            output.chmod(0o600)
            output.replace(cached)
        _prune_cache(root, cached)
        return cached
    finally:
        if lock is not None:
            lock.close()
        _conversion_slot.release()
