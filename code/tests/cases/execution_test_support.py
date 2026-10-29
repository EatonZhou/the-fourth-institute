"""Small fixtures for exercising one Worker claim without a daemon, and the scheduler's clock."""
import importlib
import pkgutil

from management_portal import execution


def run_worker_once(worker):
    claim = worker.claim(wait_seconds=0.1)
    if not claim.get("claimed"):
        return False
    worker.execute_claim(claim)
    return True


def scheduler_modules():
    """The Scheduler's module and those of its components."""
    for module in pkgutil.iter_modules(execution.__path__):
        if module.name == "scheduler" or module.name.startswith("scheduler_"):
            yield importlib.import_module(f"{execution.__name__}.{module.name}")


def patch_scheduler_clock(monkeypatch, clock):
    """Make every scheduler component read the current time from ``clock``."""
    for module in scheduler_modules():
        if hasattr(module, "execution_timestamp"):
            monkeypatch.setattr(module, "execution_timestamp", clock)


def patch_scheduler_time(monkeypatch, time_module):
    """Give every scheduler component ``time_module`` in place of the ``time`` module."""
    for module in scheduler_modules():
        if hasattr(module, "time"):
            monkeypatch.setattr(module, "time", time_module)
