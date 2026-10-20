"""Administrative readiness and detached installation of Resident embeddings."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime, timezone

from management_common.embedding_feature import enabled, model_directory, platform_issue
from management_common import process_runtime
from management_common.portal_paths import path as portal_path
from management_portal.society_service import SocietyServiceClient, get_society_service
from management_portal.society_service.config import society_service_mode, society_service_socket

ROOT = Path(__file__).resolve().parents[1]
MODEL_FILES = ("model_optimized.onnx", "tokenizer.json", "config.json", "tokenizer_config.json", "special_tokens_map.json")


def runtime_python() -> Path:
    # Preserve the virtual environment's executable path: resolving its symlink
    # would select the base interpreter instead of this Portal instance's venv.
    return Path(sys.executable).absolute()


def job_file() -> Path:
    return portal_path("instances_root") / "settings" / "resident_embedding_install.json"


def _write_job(data: dict) -> None:
    target = job_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def job_status() -> dict:
    try:
        data = json.loads(job_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"state": "idle"}
    except (OSError, ValueError):
        return {"state": "idle"}


def readiness() -> dict:
    missing = []
    issue = platform_issue()
    python = runtime_python()
    if not python.is_file():
        missing.append("当前 Portal 运行时 Python")
    else:
        try:
            probe = process_runtime.run_process(
                [str(python), "-c", "import numpy, onnxruntime, tokenizers"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=20, check=False,
            )
            if probe.returncode:
                missing.append("numpy、onnxruntime、tokenizers")
        except subprocess.TimeoutExpired:
            missing.append("当前 Portal 运行时依赖检查超时")
        except OSError as exc:
            missing.append(f"当前 Portal 运行时 Python 无法启动：{exc}")
    directory = model_directory()
    missing.extend(name for name in MODEL_FILES if not (directory / name).is_file())
    return {"enabled": enabled(), "ready": not missing and not issue,
            "active": enabled() and not missing and not issue,
            "missing": missing, "installable": issue is None, "platform_issue": issue or "",
            "model_directory": str(directory), "job": job_status()}


def change_enabled(value: bool) -> dict:
    if not isinstance(value, bool):
        raise ValueError("enabled must be a boolean")
    if value:
        state = readiness()
        if not state["ready"]:
            return {**state, "needs_install": state["installable"]}
    get_society_service().resident.set_resident_embeddings_enabled(value)
    if not value and society_service_mode() != "local":
        current = society_service_socket()
        for instance in ("dev", "release"):
            other = portal_path("instances_root") / instance / "society-service.sock"
            if other != current and other.exists():
                SocietyServiceClient(other).resident.set_resident_embeddings_enabled(False)
    return readiness()


def _reap_install(process, started_at: str) -> None:
    """Release installer descendants and record an unexpected installer exit."""
    error = ""
    try:
        code = process.wait()
        error = f"Embedding installer exited without a completion record (exit {code})"
    except Exception as exc:
        error = str(exc)
    finally:
        try:
            process_runtime.terminate_process_tree(process, force=True)
        except Exception as exc:
            error = f"Embedding installer cleanup failed: {exc}"
            logging.getLogger(__name__).error("%s", error)
    current = job_status()
    # The installer owns its complete/failed record; never overwrite a new job.
    if current.get("state") == "running" and current.get("started_at") == started_at:
        _write_job({**current, "state": "failed", "error": error})


def start_install() -> dict:
    issue = platform_issue()
    if issue:
        raise ValueError(issue)
    current = job_status()
    if current.get("state") == "running":
        return current
    target = job_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    log_path = target.with_suffix(".log")
    created = datetime.now(timezone.utc).isoformat()
    _write_job({"state": "running", "started_at": created})
    process = None
    try:
        installer = ROOT / "scripts" / "install_portal_embeddings.py"
        if not installer.is_file():
            raise FileNotFoundError(f"Embedding installer is missing: {installer}")
        with log_path.open("ab") as log:
            environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            process = process_runtime.spawn_process(
                [str(runtime_python()), str(installer)], cwd=str(ROOT),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=environment, kill_on_owner_exit=False,
            )
        threading.Thread(target=_reap_install, args=(process, created),
                         name="embedding-installer", daemon=True).start()
        return {"state": "running", "started_at": created, "pid": process.pid}
    except Exception as exc:
        try:
            if process is not None:
                process_runtime.terminate_process_tree(process, force=True)
        finally:
            _write_job({"state": "failed", "error": str(exc), "started_at": created})
        raise
