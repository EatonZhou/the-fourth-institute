"""Parameters of the automatic Harness policy, per Backend.

Whether a feature can run on a Backend is decided by capabilities alone
(``harness_capabilities``).  How a feature is used once it can run — from
which load it is switched on, its thresholds and budgets — is a parameter.

Every Backend starts from ``DEFAULT_POLICY``.  A Backend gets a value of its
own only where measurements call for one, and the measurement is written next
to the value.  The policy code reads these parameters; it never asks which
Backend it is working for.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class HarnessPolicyParameters:
    # Share of the context budget in use from which the session's history is
    # protected: early compaction is armed and the offloading and
    # deduplication thresholds tighten.
    protect_history_pressure_percent: float = 52.0
    # Share of the model's context window at which the Provider is asked to
    # compact by itself.
    compaction_threshold_percent: int = 70
    # Upper bound, in tokens, of that compaction threshold.  0: the share of
    # the window alone decides.  With a bound, the context budget that
    # "pressure" is measured against is the bound divided by the share.
    compaction_max_context_tokens: int = 0
    # Tool calls in the previous turn from which a turn counts as tool-heavy
    # or as a tool storm.
    high_tool_load_calls: int = 32
    # Where the Backend takes input while a turn runs: whether the advice to
    # batch reads is given there, as reminders when fragmented reads are
    # observed, instead of by the standing prompt policy.  Other reminders
    # (failure loops, context pressure, a silent Provider) go out during the
    # turn wherever the Backend takes them, whatever this says.
    granularity_reminders_during_turn: bool = True
    # Size, in bytes, from which a Portal tool output is offloaded to an
    # artifact.  Under context pressure the trigger tightens to three quarters
    # (from 45%) and to half (from 65%, or after a turn with large outputs).
    offload_trigger_bytes: int = 64 * 1024
    # Share of the context budget in use from which a turn's reasoning effort
    # is lowered (high and above to medium) where the Adapter can change it
    # for one turn.
    reasoning_downgrade_pressure_percent: float = 52.0


DEFAULT_POLICY = HarnessPolicyParameters()


@dataclass(frozen=True)
class Tunable:
    """A parameter the Harness evolution may change, with its safe range.

    A challenger changes one tunable by one ``step`` from the champion;
    values outside ``minimum``..``maximum`` are never run.  ``off`` is a value
    allowed outside the range that switches the behaviour off (0 tokens: no
    absolute compaction bound); evolution never steps out of it.
    """

    name: str
    minimum: float = 0
    maximum: float = 0
    step: float = 0
    kind: str = "int"
    off: float | None = None

    def valid(self, value: object) -> bool:
        if self.kind == "bool":
            return isinstance(value, bool)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if self.kind == "int" and int(value) != value:
            return False
        if self.off is not None and value == self.off:
            return True
        return self.minimum <= value <= self.maximum

    def neighbours(self, current: object) -> list[object]:
        """The values one step away from ``current`` that are in range."""

        if self.kind == "bool":
            return [not bool(current)]
        values: list[object] = []
        base = float(current)  # type: ignore[arg-type]
        if self.off is not None and base == self.off:
            # A Backend that has the behaviour off keeps it off: switching it
            # on is a person's decision (``harness_evolution.py start``).
            return []
        else:
            candidates = [base - self.step, base + self.step]
        for value in candidates:
            value = int(value) if self.kind == "int" else round(value, 3)
            if self.valid(value):
                values.append(value)
        return values


TUNABLES: dict[str, Tunable] = {
    item.name: item
    for item in (
        Tunable("compaction_threshold_percent", 55, 85, 5),
        Tunable("compaction_max_context_tokens", 100_000, 800_000, 100_000, off=0),
        Tunable("protect_history_pressure_percent", 40, 70, 6, kind="float"),
        Tunable("high_tool_load_calls", 16, 64, 8),
        Tunable("offload_trigger_bytes", 16 * 1024, 128 * 1024, 16 * 1024),
        Tunable("reasoning_downgrade_pressure_percent", 40, 88, 6, kind="float"),
    )
}


def validate_overrides(overrides: object) -> dict[str, object]:
    """Return ``overrides`` if every entry is a tunable within its range."""

    if not isinstance(overrides, Mapping):
        raise ValueError("Harness policy overrides must be an object")
    clean: dict[str, object] = {}
    for name, value in overrides.items():
        tunable = TUNABLES.get(str(name))
        if tunable is None:
            raise ValueError(f"not a tunable Harness parameter: {name}")
        if not tunable.valid(value):
            raise ValueError(f"Harness parameter {name} out of range: {value!r}")
        clean[str(name)] = value
    return clean


def parse_override(text: str) -> tuple[str, object]:
    """Read one ``name=value`` override as a command line names it."""

    name, separator, raw = str(text).partition("=")
    name = name.strip()
    tunable = TUNABLES.get(name)
    if not separator or tunable is None:
        raise ValueError(f"expected <tunable>=<value>, one of {sorted(TUNABLES)}: {text}")
    raw = raw.strip()
    value: object
    if tunable.kind == "bool":
        if raw.casefold() not in {"true", "false"}:
            raise ValueError(f"{name} takes true or false")
        value = raw.casefold() == "true"
    elif tunable.kind == "int":
        value = int(raw)
    else:
        value = float(raw)
    if not tunable.valid(value):
        raise ValueError(f"Harness parameter {name} out of range: {value!r}")
    return name, value


def with_overrides(
    parameters: HarnessPolicyParameters,
    overrides: Mapping[str, object],
) -> HarnessPolicyParameters:
    """The Backend's parameters with validated tunable overrides applied."""

    clean = validate_overrides(overrides)
    return replace(parameters, **clean) if clean else parameters

# A context of this size is what a 272k-token window compacts at with the
# default 70% share (Codex: 181k of an effective 258k window).  A Backend whose
# model has a one-million-token window and compacts quickly gets this as an
# absolute bound.
_LARGE_WINDOW_COMPACTION_TOKENS = 200_000

# Claude Code, DSH and Kimi Code take input during a turn since 2026-10-03.
# Their read-batch advice stays the standing prompt policy, which is what
# their measurements ran with: in the 2026-10-03 baselines the automatic
# policy, which was little more than that prompt, used 34% less input than
# all optional features off on Claude Code and 42% less on DSH (on Kimi Code
# the runs were too short to tell).  Reminders during a turn have not been
# measured against it on these Backends.
_PROMPT_GRANULARITY = {"granularity_reminders_during_turn": False}

BACKEND_POLICY: dict[str, HarnessPolicyParameters] = {
    "kimi": replace(DEFAULT_POLICY, **_PROMPT_GRANULARITY),
    # Claude Code on a one-million-token model (claude-sonnet-5, claude_one):
    # in the 2026-10-03 baseline the context grew from 113k to 585k tokens
    # over nine audit chapters and the input per chapter grew with it, from
    # 0.43M to 2.69M tokens; 70% of the window (700k) was never reached, so
    # the Provider never compacted.
    #
    # Claude Code compacts by having the model write a summary in the
    # session's own model, thinking and effort (to read the main loop's prompt
    # cache): 140-260 seconds each, whatever the context size.  So the bound
    # trades tokens for compactions.  Measured on the same 13 chapters
    # (2026-10-03), against 42.1M input tokens, 0.77M uncached and 33 minutes
    # without compaction:
    #   200k: 7 compactions (25 minutes of summaries), 7.7M input, 1.10M
    #         uncached; about 45% of the cost at Claude's price ratios
    #   400k: 2 compactions (6 minutes), 18.5M input, 0.96M uncached, context
    #         peak 347k; about 65% of the cost; report unchanged
    # 400k keeps most of the saving for a quarter of the waiting.
    "claude": replace(DEFAULT_POLICY, compaction_max_context_tokens=400_000, **_PROMPT_GRANULARITY),
    # DeepSeek Harness on deepseek-v4-pro (one-million-token window): the
    # same baseline grew the context from 49k to 476k tokens over 13 chapters
    # and the input per chapter from 0.39M to 3.37M tokens.  With the bound
    # (2026-10-03, 13 chapters each): context at the end 197k instead of
    # 492k, input 18.5M instead of 24.1M tokens, report score unchanged, and
    # the two compactions did not lengthen the run (30 against 28 minutes).
    #
    "dsh": replace(DEFAULT_POLICY, compaction_max_context_tokens=_LARGE_WINDOW_COMPACTION_TOKENS, **_PROMPT_GRANULARITY),
}


@dataclass(frozen=True)
class CompactionReplayModel:
    """How a Backend's own compaction follows the tunables, for offline replay.

    ``sticky_absolute_limit``: the bound is a token count the session keeps;
    a later turn can only lower it (Claude Code's auto-compact window).
    Otherwise the limit is the threshold share of the context window, capped
    by the bound (Codex).  ``native_compact_tokens``: where the Provider
    compacts when nothing was configured; 0 means it is learnt from the
    recorded compactions.
    """

    sticky_absolute_limit: bool = False
    native_compact_tokens: int = 0


# Only these Backends' compactions can be replayed from recorded calls.
COMPACTION_REPLAY_MODELS: dict[str, CompactionReplayModel] = {
    "codex": CompactionReplayModel(),
    # Claude Code without a configured limit compacts near its 1M-token window.
    "claude": CompactionReplayModel(sticky_absolute_limit=True, native_compact_tokens=800_000),
}


def compaction_replay_model(provider_id: object) -> CompactionReplayModel | None:
    """The replay model of a Backend's compaction (None: it cannot be replayed)."""

    return COMPACTION_REPLAY_MODELS.get(str(provider_id or "").strip())


def policy_parameters(provider_id: object) -> HarnessPolicyParameters:
    """The policy parameters of a Backend (the defaults for an unknown one)."""

    return BACKEND_POLICY.get(str(provider_id or "").strip(), DEFAULT_POLICY)


def context_budget_tokens(
    parameters: HarnessPolicyParameters,
    context_window_tokens: int,
) -> int:
    """The context size that counts as 100% pressure for this Backend."""

    window = max(0, int(context_window_tokens or 0))
    bound = max(0, int(parameters.compaction_max_context_tokens or 0))
    if not bound:
        return window
    share = max(1, min(99, int(parameters.compaction_threshold_percent)))
    budget = (bound * 100) // share
    return min(window, budget) if window else budget

HARNESS_EXPERIMENT_SCHEMA = "tfi.agent-harness-live-experiment.v1"
