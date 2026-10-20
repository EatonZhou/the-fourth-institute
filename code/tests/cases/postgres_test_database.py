"""Choose the PostgreSQL server pytest uses; it is never the server Portal runs on.

An explicit `PORTAL_POSTGRES_TEST_DSN` is used when it points somewhere other
than the Portal database server. Without one, the session starts a disposable
PostgreSQL in its own temporary directory and removes it when the session
ends. A temporary directory or cluster whose pytest process died (for
example under `timeout`) is removed by the next session that starts.
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit
from management_common.postgres_configuration import postgres_env_file

PRODUCTION_ENVIRONMENT_FILE = postgres_env_file()
POSTGRES_ROOT = Path(os.getenv("PORTAL_TEST_POSTGRES_ROOT", str(Path.home() / ".local/postgresql-16")))
CLUSTER_PARENT = Path("/tmp")  # short enough for the Unix socket path limit
TEMP_PREFIX = "tfi-portal-pytest-"
CLUSTER_PREFIX = f"{TEMP_PREFIX}pg-"
OWNER_FILE = "owner.pid"
# A directory without an owner file was made before sessions wrote one.
UNOWNED_AGE_SECONDS = 24 * 3600


def server_address(dsn: str) -> tuple[str, str]:
    """The (host, port) a libpq URL connects to; socket directories are resolved."""
    parts = urlsplit(dsn)
    query = parse_qs(parts.query)
    host = unquote((query.get("host") or [parts.hostname or ""])[-1]) or "localhost"
    port = str((query.get("port") or [parts.port or ""])[-1] or "5432")
    if host.startswith("/"):
        host = os.path.realpath(host)
    elif host in {"127.0.0.1", "::1"}:
        host = "localhost"
    return host, port


def _environment_file_url(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() == "PORTAL_DATABASE_URL":
            return value.strip().strip("'\"")
    return ""


def production_addresses(environ: dict[str, str]) -> set[tuple[str, str]]:
    inherited = str(environ.get("PORTAL_DATABASE_URL") or "").strip()
    # A pytest session hands its chosen test database to child processes in
    # both variables; that inherited URL is the test database, not Portal's.
    if inherited == str(environ.get("PORTAL_POSTGRES_TEST_DSN") or "").strip():
        inherited = ""
    urls = (inherited, _environment_file_url(PRODUCTION_ENVIRONMENT_FILE))
    return {server_address(url) for url in urls if url}


def refuse_production(dsn: str, production: set[tuple[str, str]]) -> None:
    if server_address(dsn) in production:
        raise RuntimeError(
            "PORTAL_POSTGRES_TEST_DSN points at the Portal database server; "
            "pytest never creates schemas there. Unset it to use a disposable "
            "test database, or point it at a separate PostgreSQL server."
        )


def _postgres_environment() -> dict[str, str]:
    library = os.environ.get("PORTAL_TEST_POSTGRES_LIB") or str(POSTGRES_ROOT / (
        "lib" if (POSTGRES_ROOT / "bin/initdb").is_file() else "usr/lib/x86_64-linux-gnu"))
    inherited = os.environ.get("LD_LIBRARY_PATH", "")
    return {**os.environ, "LD_LIBRARY_PATH": library + (f":{inherited}" if inherited else "")}


def _binary(name: str) -> Path:
    if os.environ.get("PORTAL_TEST_POSTGRES_BIN"):
        return Path(os.environ["PORTAL_TEST_POSTGRES_BIN"]) / name
    if (POSTGRES_ROOT / "bin/initdb").is_file():
        return POSTGRES_ROOT / "bin" / name
    return POSTGRES_ROOT / "usr/lib/postgresql/16/bin" / name


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    return b"pytest" in command or b"python" in command


def stop_cluster(root: Path) -> None:
    data = root / "data"
    if (data / "postmaster.pid").is_file():
        stopped = subprocess.run(
            [str(_binary("pg_ctl")), "-D", str(data), "-m", "immediate", "-w", "stop"],
            env=_postgres_environment(), capture_output=True, check=False,
        )
        if stopped.returncode != 0:
            try:
                os.kill(int((data / "postmaster.pid").read_text().split()[0]), signal.SIGQUIT)
            except (OSError, ValueError, IndexError):
                pass
    shutil.rmtree(root, ignore_errors=True)


def mark_owner(root: Path) -> None:
    (root / OWNER_FILE).write_text(str(os.getpid()))


def _abandoned(root: Path, now: float) -> bool:
    try:
        if not root.is_dir() or root.is_symlink() or root.stat().st_uid != os.getuid():
            return False
        owner = int((root / OWNER_FILE).read_text().strip())
    except FileNotFoundError:
        try:
            return now - root.stat().st_mtime > UNOWNED_AGE_SECONDS
        except OSError:
            return False
    except (OSError, ValueError):
        return False
    return owner != os.getpid() and not _process_alive(owner)


def sweep_abandoned_roots(parent: Path = CLUSTER_PARENT) -> list[Path]:
    """Remove the temporary directories and clusters of pytest sessions that are gone."""
    now = time.time()
    removed = []
    for root in sorted(parent.glob(f"{TEMP_PREFIX}*")):
        if _abandoned(root, now):
            stop_cluster(root)
            removed.append(root)
    return removed


def start_disposable_cluster() -> str:
    sweep_abandoned_roots()
    if not _binary("initdb").is_file():
        raise RuntimeError(
            "PostgreSQL tests need PORTAL_POSTGRES_TEST_DSN or the PostgreSQL 16 "
            f"binaries under {POSTGRES_ROOT} for a disposable test database"
        )
    root = Path(tempfile.mkdtemp(prefix=CLUSTER_PREFIX, dir=CLUSTER_PARENT))
    owner = os.getpid()
    mark_owner(root)
    # A forked child inherits exit handlers; only the owning session stops the server.
    atexit.register(lambda: os.getpid() == owner and stop_cluster(root))
    environment = _postgres_environment()
    socket = root / "socket"
    socket.mkdir()
    subprocess.run(
        [str(_binary("initdb")), "-D", str(root / "data"), "-U", "postgres", "-A", "trust",
         "-E", "UTF8", "--locale=C.UTF-8", "--no-sync"],
        env=environment, capture_output=True, check=True,
    )
    # Match the Portal server's locale and time zone so results do not depend
    # on which server a test ran against; durability is irrelevant here.
    options = " ".join((
        f"-k {socket}", "-p 5432", "-c listen_addresses=", "-c max_connections=200",
        "-c shared_buffers=32MB", "-c fsync=off", "-c synchronous_commit=off",
        "-c full_page_writes=off", "-c timezone=Asia/Hong_Kong",
    ))
    subprocess.run(
        [str(_binary("pg_ctl")), "-D", str(root / "data"), "-l", str(root / "postgres.log"),
         "-w", "-t", "60", "-o", options, "start"],
        env=environment, capture_output=True, check=True,
    )
    return f"postgresql://postgres@/postgres?host={quote(str(socket), safe='')}&port=5432"


def choose_test_database(environ: dict[str, str]) -> str:
    """Return the DSN this pytest session uses, starting a disposable server if needed."""
    production = production_addresses(environ)
    explicit = str(environ.get("PORTAL_POSTGRES_TEST_DSN") or "").strip()
    if explicit:
        refuse_production(explicit, production)
        return explicit
    return start_disposable_cluster()
