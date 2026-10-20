from __future__ import annotations


class SearchRunSuperseded(RuntimeError):
    pass


def run_generation(job: dict) -> int:
    generation = job.get("run_generation", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("invalid semantic search run generation")
    return generation


def require_current_run(job: dict | None, expected_generation: int) -> None:
    if (not job or job.get("deleted") or run_generation(job) != expected_generation
            or job.get("status") not in {"queued", "running"}):
        raise SearchRunSuperseded("semantic search run is no longer current")
