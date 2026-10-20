from __future__ import annotations

from dataclasses import replace
from typing import Any

from .harness import HarnessFeatureOutcome, ProviderErrorRecoveryPolicy
from .harness_capabilities import ERROR_CONTINUATION, requires


PROVIDER_ERROR_CONTINUE_FEATURE_ID = "provider_error_continue"


class ProviderErrorContinueFeature:
    """Arm one ``continue`` in the same session, sent 3 seconds after a Provider error."""

    feature_id = PROVIDER_ERROR_CONTINUE_FEATURE_ID
    # Only an Adapter that continues a failed turn in the same session can
    # consume the recovery policy this feature attaches.
    requires = requires({ERROR_CONTINUATION})

    def apply(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        observer: Any = None,
    ) -> HarnessFeatureOutcome:
        del context, observer
        attempts = 1
        updated = replace(
            invocation,
            provider_error_recovery=ProviderErrorRecoveryPolicy(
                max_continue_attempts=attempts,
            ),
        )
        return HarnessFeatureOutcome(
            invocation=updated,
            status="applied",
            reason="provider_error_continue_armed",
            metrics={"max_continue_attempts": attempts},
            action_applied=False,
        )
