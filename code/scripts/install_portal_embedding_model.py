#!/usr/bin/env python3
"""Install the pinned Resident embedding model into Portal's data directory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from management_common.embedding_feature import model_directory  # noqa: E402

REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
REPOSITORY = "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
FILES = ("model_optimized.onnx", "tokenizer.json", "config.json", "tokenizer_config.json", "special_tokens_map.json")
MODEL_SHA256 = "634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99"
LEGACY_CACHE = Path(
    "/data/weight/fastembed/models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q/"
    f"snapshots/{REVISION}"
)


def valid_file(path: Path, name: str) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if name != "model_optimized.onnx":
        return True
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == MODEL_SHA256


def install(destination: Path | None = None) -> Path:
    destination = destination or model_directory()
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = destination / name
        if valid_file(target, name):
            continue
        temporary = destination / f".{name}.{os.getpid()}.download"
        try:
            cached = LEGACY_CACHE / name
            if cached.is_file():
                shutil.copyfile(cached, temporary)
            else:
                url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}"
                print(f"Downloading {name} from {REPOSITORY}@{REVISION}", flush=True)
                with urllib.request.urlopen(url, timeout=270) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            if not valid_file(temporary, name):
                raise RuntimeError(f"Model file verification failed: {name}")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    print(f"Resident embedding model ready: {install()}")
