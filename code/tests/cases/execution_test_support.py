"""Small fixtures for exercising one Worker claim without a daemon."""

def run_worker_once(worker):
    claim = worker.claim(wait_seconds=0.1)
    if not claim.get("claimed"):
        return False
    worker.execute_claim(claim)
    return True
