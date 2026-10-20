"""Local sentence embeddings for Resident search.

paraphrase-multilingual-MiniLM-L12-v2 (quantized ONNX) with mean pooling
and L2 normalization: the same vectors fastembed produced for the
standalone service, so stored vectors stay valid.  The model (~750 MB
resident) loads on first use and unloads after ``idle_seconds`` without
use.  ``onnxruntime``, ``tokenizers`` and ``numpy`` are imported only then;
see ``requirements-society-service.txt``.
"""
from __future__ import annotations

import json
import os
import threading
import time
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from management_common.embedding_feature import enabled as embeddings_enabled, model_directory

MODEL_VERSION = "onnx:paraphrase-multilingual-MiniLM-L12-v2:mean-pooling:l2-v1"
REQUIRED_MODEL_FILES = ("model_optimized.onnx", "tokenizer.json", "config.json", "tokenizer_config.json", "special_tokens_map.json")


def resident_embeddings_available() -> bool:
    """Whether this runtime can serve Resident vector search and embedding jobs."""

    if not embeddings_enabled():
        return False
    if not all(find_spec(module) for module in ("numpy", "onnxruntime", "tokenizers")):
        return False
    model_dir = model_directory()
    return all((model_dir / name).is_file() for name in REQUIRED_MODEL_FILES)


class ResidentEmbedder:
    model_version = MODEL_VERSION

    def __init__(
        self, *, model_dir: Path | str | None = None, threads: int = 4, idle_seconds: float = 900.0,
    ) -> None:
        self.model_dir = Path(model_dir or model_directory())
        self.threads = max(1, int(threads))
        self.idle_seconds = max(30.0, float(idle_seconds))
        self._lock = threading.RLock()
        self._model: tuple[Any, Any, set[str]] | None = None
        self._last_used = 0.0
        self._unloader: threading.Timer | None = None

    def embed_query(self, text: str) -> list[float]:
        return self._embed([str(text or "")])[0]

    def embed_document(self, text: str) -> list[float]:
        return self._embed([str(text or "")])[0]

    def unload(self) -> None:
        """Drop the ONNX session and tokenizer, including the idle timer."""
        with self._lock:
            timer, self._unloader = self._unloader, None
            if timer is not None:
                timer.cancel()
            self._model = None
            self._last_used = 0.0

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import numpy as np

        with self._lock:
            tokenizer, session, input_names = self._load()
            self._last_used = time.monotonic()
            self._schedule_unload()
            encoded = tokenizer.encode_batch(texts)
            ids = np.array([item.ids for item in encoded], dtype=np.int64)
            mask = np.array([item.attention_mask for item in encoded], dtype=np.int64)
            feed = {"input_ids": ids}
            if "attention_mask" in input_names:
                feed["attention_mask"] = mask
            if "token_type_ids" in input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = session.run(None, feed)[0]
        weights = mask[..., None].astype(hidden.dtype)
        pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
        output = []
        for vector in pooled:
            norm = float(np.linalg.norm(vector)) or 1.0
            output.append([float(value) / norm for value in vector])
        return output

    def _load(self) -> tuple[Any, Any, set[str]]:
        if self._model is not None:
            return self._model
        import onnxruntime
        from tokenizers import AddedToken, Tokenizer

        config = json.loads((self.model_dir / "config.json").read_text(encoding="utf-8"))
        tokenizer_config = json.loads((self.model_dir / "tokenizer_config.json").read_text(encoding="utf-8"))
        special_tokens = json.loads((self.model_dir / "special_tokens_map.json").read_text(encoding="utf-8"))
        max_length = min(
            int(tokenizer_config.get("model_max_length") or 10**9),
            int(tokenizer_config.get("max_length") or 10**9),
        )
        tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=max_length)
        if not tokenizer.padding:
            tokenizer.enable_padding(
                pad_id=int(config.get("pad_token_id") or 0), pad_token=str(tokenizer_config["pad_token"]),
            )
        for token in special_tokens.values():
            tokenizer.add_special_tokens([token] if isinstance(token, str) else [AddedToken(**token)])
        options = onnxruntime.SessionOptions()
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = self.threads
        session = onnxruntime.InferenceSession(
            str(self.model_dir / "model_optimized.onnx"), sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._model = (tokenizer, session, {item.name for item in session.get_inputs()})
        return self._model

    def _schedule_unload(self) -> None:
        if self._unloader is None:
            self._schedule_unload_after(self.idle_seconds)

    def _unload_if_idle(self) -> None:
        with self._lock:
            self._unloader = None
            if self._model is None:
                return
            idle = time.monotonic() - self._last_used
            if idle >= self.idle_seconds:
                self._model = None
                return
            self._schedule_unload_after(self.idle_seconds - idle)

    def _schedule_unload_after(self, seconds: float) -> None:
        timer = threading.Timer(max(1.0, seconds), self._unload_if_idle)
        timer.daemon = True
        timer.name = "resident-embedder-unload"
        self._unloader = timer
        timer.start()


_SHARED: ResidentEmbedder | None = None
_SHARED_LOCK = threading.Lock()
# Search, embedding jobs and switch changes share this gate. A disable response
# is sent only after any in-flight vector operation has finished and unloaded.
EMBEDDING_RUNTIME_LOCK = threading.RLock()


def shared_embedder() -> ResidentEmbedder:
    """The process's one embedder: one model in memory however many callers."""

    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = ResidentEmbedder()
        return _SHARED


def unload_shared_embedder() -> None:
    """Release the model held by this SocietyService process."""

    global _SHARED
    with _SHARED_LOCK:
        embedder = _SHARED
        _SHARED = None
    if embedder is not None:
        embedder.unload()
