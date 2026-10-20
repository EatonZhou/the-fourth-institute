"""Versioned NumPy/JSON results, atomic checkpoints and lazy frame reading."""
import csv
import json
from pathlib import Path

import numpy as np

from .core import LeniaConfig

SCHEMA = "lenia.simulation.v1"


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def save_checkpoint(path, state, step, metadata):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("wb") as handle:
        np.savez(handle, state=state, step=np.int64(step),
                 metadata_json=json.dumps(metadata, allow_nan=False))
    temp.replace(path)


def load_checkpoint(path):
    """Return (state, absolute_step, metadata). No pickle or external files."""
    with np.load(path, allow_pickle=False) as data:
        state = data["state"].copy()
        raw_step = data["step"]
        if raw_step.shape != () or raw_step.dtype.kind not in "iu" or raw_step.item() < 0:
            raise ValueError("checkpoint step must be a nonnegative integer scalar")
        step = int(raw_step)
        metadata = json.loads(str(data["metadata_json"].item()))
    if metadata.get("schema") != SCHEMA:
        raise ValueError("unsupported checkpoint schema")
    config = LeniaConfig(**metadata["config"])
    if state.shape != (config.size, config.size) or state.dtype != np.dtype(config.dtype):
        raise ValueError("checkpoint shape/dtype does not match configuration")
    if not np.isfinite(state).all() or np.any((state < 0) | (state > 1)):
        raise ValueError("checkpoint state must be finite and in [0, 1]")
    return state, step, metadata


class RunRecorder:
    """One job owns one output directory; frames are immutable full precision."""
    def __init__(self, directory, metadata):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        reserved = ("manifest.json", "checkpoint.npz", "frames", "frames.json",
                    "metrics.csv", "progress.json", "result.json")
        if any((self.directory / name).exists() for name in reserved):
            raise FileExistsError("simulation output already exists; use a new LENIA_RUN_DIR")
        (self.directory / "frames").mkdir()
        self.metadata = metadata
        self.records = []
        self.metrics = (self.directory / "metrics.csv").open("x", newline="")
        self.csv = csv.writer(self.metrics)
        self.csv.writerow(["step", "sim_time", "mass", "occupied_cells", "min", "max", "applied_intensity"])
        atomic_json(self.directory / "manifest.json", metadata)

    def sample(self, state, step, intensity):
        if self.records and self.records[-1]["step"] == step:
            return
        relative = f"frames/{step:012d}.npy"
        path = self.directory / relative
        temp = path.with_suffix(".tmp")
        with temp.open("wb") as handle:
            np.save(handle, state, allow_pickle=False)
        temp.replace(path)
        self.records.append({"step": step, "path": relative})
        self.csv.writerow([step, step * self.metadata["config"]["dt"], float(state.sum()),
                           int(np.count_nonzero(state > 0.01)), float(state.min()),
                           float(state.max()), intensity])

    def checkpoint(self, state, step, status):
        save_checkpoint(self.directory / "checkpoint.npz", state, step, self.metadata)
        self.metrics.flush()
        atomic_json(self.directory / "frames.json", {"schema": SCHEMA, "frames": self.records})
        atomic_json(self.directory / "progress.json", {
            "schema": SCHEMA, "step": step, "status": status,
            "mass": float(state.sum()), "frames": len(self.records)})

    def close(self):
        self.metrics.close()


class RunReader:
    """Read committed frame indexes; refresh() also works while a job runs."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.metadata = json.loads((self.directory / "manifest.json").read_text())
        if self.metadata.get("schema") != SCHEMA:
            raise ValueError("unsupported result schema")
        self.refresh()

    def refresh(self):
        index = self.directory / "frames.json"
        self.frames = json.loads(index.read_text())["frames"] if index.exists() else []

    def read_frame(self, index):
        """Full precision state; copy it before changing a memmapped frame."""
        return np.load(self.directory / self.frames[index]["path"], mmap_mode="r", allow_pickle=False)

    def iter_frames(self, stride=1):
        if stride < 1:
            raise ValueError("stride must be positive")
        for index in range(0, len(self.frames), stride):
            yield self.frames[index]["step"], self.read_frame(index)

    def checkpoint(self):
        return load_checkpoint(self.directory / "checkpoint.npz")
