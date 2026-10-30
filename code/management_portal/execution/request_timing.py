"""Optional timing observations. This module never carries Provider traffic.

Adapters may copy timestamps from an actual local request boundary or a native
request event. Missing timestamps stay unknown. There is no URL rewriting,
HTTP listener, response inspection or synchronous remote lookup.
"""
from .service_settings import ExecutionSettings


class RequestTimingModule:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else ExecutionSettings()

    def configure(self, enabled):
        return self.settings.update(enabled)


def observed_timing(value):
    """Consume a small copy at persistence time, never on the transport path."""
    if not isinstance(value, dict) or not value:
        return {}
    try:
        if not ExecutionSettings(initialize=False).model_request_timing_enabled():
            return {}
        return {key: value.get(key) for key in ('started_at', 'finished_at')}
    except Exception:
        # A broken observation setting must not discard a model's response.
        return {}
