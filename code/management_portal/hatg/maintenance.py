"""Service-owned maintenance of instruction recovery receipts."""
from __future__ import annotations

import logging
import threading

from .kernel import InstructionKernel


class InstructionMaintenance:
    def __init__(self, kernel: InstructionKernel) -> None:
        self.kernel = kernel
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"{kernel.owner}-instruction-maintenance", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.kernel.maintain()
            except Exception:
                logging.getLogger(__name__).exception("%s instruction maintenance failed", self.kernel.owner)
            if self._stop.wait(60):
                return


def recover_projections(failures, invocation) -> None:
    """Other owners carry Lineage's committed projection receipts over RPC."""
    from management_portal.society_service import get_society_service

    get_society_service().lineage.retry_lineage_projections(failures, invocation.caller)
