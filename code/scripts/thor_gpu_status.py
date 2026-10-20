#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


DEFAULT_SSH_CONFIG = Path.home() / ".config/osforagent/thor_ssh_config"
DEFAULT_SSH_HOST = "thor"


REMOTE_SCRIPT = r"""
import csv
import json
import shutil
import socket
import subprocess
from datetime import datetime, timezone


BUSY_GPU_UTIL_PERCENT = 50
BUSY_MEMORY_USED_PERCENT = 50
EMPTY_GPU_UTIL_PERCENT = 5
EMPTY_MEMORY_USED_PERCENT = 5


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run(args):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=8)
    except Exception as exc:
        return "", str(exc), 1
    return result.stdout, result.stderr, result.returncode


def parse_csv_lines(text):
    return [row for row in csv.reader(text.splitlines(), skipinitialspace=True) if row]


def clean(value):
    text = str(value or "").strip()
    if text in {"", "[N/A]", "N/A", "Not Supported"}:
        return None
    return text


def as_int(value):
    text = clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def as_float(value):
    text = clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except Exception:
        return None


def memory_used_percent(gpu):
    used = gpu.get("memory_used_mb")
    total = gpu.get("memory_total_mb")
    if used is not None and total:
        return used / total * 100
    if gpu.get("unified_memory_used_percent") is not None:
        return gpu.get("unified_memory_used_percent") or 0
    return gpu.get("memory_util_percent") or 0


def gpu_is_busy(gpu):
    return (gpu.get("gpu_util_percent") or 0) > BUSY_GPU_UTIL_PERCENT or memory_used_percent(gpu) > BUSY_MEMORY_USED_PERCENT


def gpu_state(gpu):
    util = gpu.get("gpu_util_percent") or 0
    memory = memory_used_percent(gpu)
    if util < EMPTY_GPU_UTIL_PERCENT and memory < EMPTY_MEMORY_USED_PERCENT:
        return "empty"
    if util > BUSY_GPU_UTIL_PERCENT or memory > BUSY_MEMORY_USED_PERCENT:
        return "busy"
    return "idle"


def query_unified_memory():
    values = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    parts = rest.strip().split()
                    if parts:
                        values[key] = int(parts[0]) // 1024
    except Exception:
        return {}
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return {}
    used = max(0, total - available)
    return {
        "unified_memory_total_mb": total,
        "unified_memory_used_mb": used,
        "unified_memory_used_percent": round(used / total * 100, 1),
    }


def query_gpus():
    fields = [
        "index",
        "name",
        "uuid",
        "memory.total",
        "memory.used",
        "memory.free",
        "utilization.gpu",
        "utilization.memory",
        "temperature.gpu",
        "power.draw",
        "power.limit",
    ]
    stdout, stderr, code = run([
        "nvidia-smi",
        "--query-gpu=" + ",".join(fields),
        "--format=csv,noheader,nounits",
    ])
    if code != 0:
        raise RuntimeError(stderr.strip() or stdout.strip() or "nvidia-smi query failed")
    gpus = []
    for row in parse_csv_lines(stdout):
        values = (row + [""] * len(fields))[:len(fields)]
        item = dict(zip(fields, values))
        memory_total = as_int(item.get("memory.total"))
        memory_used = as_int(item.get("memory.used"))
        memory_free = as_int(item.get("memory.free"))
        gpus.append({
            "index": as_int(item.get("index")),
            "name": clean(item.get("name")),
            "uuid": clean(item.get("uuid")),
            "memory_supported": memory_total is not None,
            "memory_total_mb": memory_total,
            "memory_used_mb": memory_used,
            "memory_free_mb": memory_free,
            "gpu_util_percent": as_int(item.get("utilization.gpu")),
            "memory_util_percent": as_int(item.get("utilization.memory")),
            "temperature_c": as_int(item.get("temperature.gpu")),
            "power_draw_w": as_float(item.get("power.draw")),
            "power_limit_w": as_float(item.get("power.limit")),
            "compute_processes": [],
            "graphics_processes": [],
        })
    return gpus


def query_compute_processes():
    stdout, stderr, code = run([
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])
    if code != 0:
        return []
    processes = []
    for row in parse_csv_lines(stdout):
        if len(row) < 4:
            continue
        gpu_uuid, pid, process_name, used_memory = row[:4]
        processes.append({
            "gpu_uuid": clean(gpu_uuid),
            "pid": as_int(pid),
            "type": "compute",
            "process_name": clean(process_name),
            "used_memory_mb": as_int(used_memory),
        })
    return processes


def query_pmon_processes():
    stdout, stderr, code = run(["nvidia-smi", "pmon", "-c", "1"])
    if code != 0:
        return []
    processes = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 10:
            continue
        gpu_index, pid, proc_type = parts[0], parts[1], parts[2]
        processes.append({
            "gpu_index": as_int(gpu_index),
            "pid": as_int(pid),
            "type": "compute" if proc_type == "C" else "graphics" if proc_type == "G" else proc_type,
            "command": parts[-1],
        })
    return processes


def query_ps(pids):
    clean_pids = sorted({str(pid) for pid in pids if pid})
    if not clean_pids:
        return {}
    stdout, stderr, code = run([
        "ps",
        "-p",
        ",".join(clean_pids),
        "-o",
        "pid=,user:40=,stat=,pcpu=,pmem=,etime=,args=",
    ])
    if code != 0:
        return {}
    details = {}
    for line in stdout.splitlines():
        parts = line.strip().split(None, 6)
        if len(parts) < 7:
            continue
        pid, user, stat, pcpu, pmem, etime, args = parts
        details[int(pid)] = {
            "user": user,
            "stat": stat,
            "cpu_percent": as_float(pcpu),
            "memory_percent": as_float(pmem),
            "elapsed": etime,
            "command_line": args,
        }
    return details


def merge_process_details(processes, ps_details):
    merged = []
    for proc in processes:
        item = dict(proc)
        detail = ps_details.get(item.get("pid")) or {}
        item.update(detail)
        merged.append(item)
    return merged


def main():
    if not shutil.which("nvidia-smi"):
        print(json.dumps({
            "status": "error",
            "generated_at": now_iso(),
            "host": socket.gethostname(),
            "error": "nvidia-smi not found",
        }))
        return

    try:
        gpus = query_gpus()
        unified_memory = query_unified_memory()
        compute = query_compute_processes()
        pmon = query_pmon_processes()
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "generated_at": now_iso(),
            "host": socket.gethostname(),
            "error": str(exc),
        }))
        return

    compute_keys = {(item.get("pid"), item.get("gpu_uuid")) for item in compute}
    graphics = [
        item for item in pmon
        if item.get("type") == "graphics" and (item.get("pid"), item.get("gpu_uuid")) not in compute_keys
    ]
    ps_details = query_ps([item.get("pid") for item in compute + graphics])
    compute = merge_process_details(compute, ps_details)
    graphics = merge_process_details(graphics, ps_details)

    by_uuid = {item.get("uuid"): item for item in gpus if item.get("uuid")}
    by_index = {item.get("index"): item for item in gpus if item.get("index") is not None}
    if unified_memory:
        for gpu in gpus:
            if not gpu.get("memory_supported"):
                gpu.update(unified_memory)
    for proc in compute:
        gpu = by_uuid.get(proc.get("gpu_uuid"))
        if gpu is not None:
            gpu["compute_processes"].append(proc)
    for proc in graphics:
        gpu = by_index.get(proc.get("gpu_index"))
        if gpu is not None:
            gpu["graphics_processes"].append(proc)

    busy_gpus = []
    state_counts = {"empty": 0, "idle": 0, "busy": 0}
    for gpu in gpus:
        state = gpu_state(gpu)
        gpu["state"] = state
        gpu_busy = state == "busy"
        gpu["busy"] = gpu_busy
        if state in state_counts:
            state_counts[state] += 1
        if gpu_busy:
            busy_gpus.append(gpu.get("index"))

    payload = {
        "status": "ok",
        "generated_at": now_iso(),
        "host": socket.gethostname(),
        "gpu_count": len(gpus),
        "busy_gpu_indexes": busy_gpus,
        "empty_gpu_count": state_counts["empty"],
        "idle_gpu_count": state_counts["idle"],
        "busy_gpu_count": state_counts["busy"],
        "gpus": gpus,
    }
    print(json.dumps(payload, ensure_ascii=False))


main()
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Return current Thor GPU usage as JSON.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ssh-config", default=DEFAULT_SSH_CONFIG)
    parser.add_argument("--host", default=DEFAULT_SSH_HOST)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--timeout", type=float, default=45.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ssh_config = Path(args.ssh_config)
    if not ssh_config.exists():
        print(json.dumps({
            "status": "error",
            "error": f"ssh config not found: {ssh_config}",
        }), file=sys.stderr)
        return 2
    command = [
        "ssh",
        "-F",
        str(ssh_config),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={max(1, int(args.timeout))}",
        args.host,
        "python3",
        "-",
    ]
    try:
        result = subprocess.run(command, input=REMOTE_SCRIPT, text=True, capture_output=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "error": f"ssh timed out after {args.timeout:g}s"}))
        return 1
    if result.returncode != 0:
        print(json.dumps({
            "status": "error",
            "error": (result.stderr or result.stdout).strip(),
        }))
        return result.returncode
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(json.dumps({
            "status": "error",
            "error": "remote script returned non-json output",
            "stdout": result.stdout,
            "stderr": result.stderr,
        }))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
