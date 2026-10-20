"""Periodic single-channel Lenia; state is (..., y, x), values in [0, 1].

Default polynomial kernel/growth reproduce the project's Orbium reference.
Stimulus is an external scalar or broadcastable field: -dt * intensity * A.
The rule parameters stay fixed unless the caller explicitly changes them.
"""
from dataclasses import dataclass
import math

import numpy as np

from .seeds import ORBIUM


@dataclass(frozen=True)
class LeniaConfig:
    size: int = 64
    radius: float = 13.0
    mu: float = 0.15
    sigma: float = 0.014
    dt: float = 0.1
    dtype: str = "float64"

    def __post_init__(self):
        if not isinstance(self.size, int) or self.size < 4:
            raise ValueError("size must be an integer >= 4")
        if not all(math.isfinite(x) for x in (self.radius, self.mu, self.sigma, self.dt)):
            raise ValueError("rule parameters must be finite")
        if not 1 < self.radius <= self.size / 2:
            raise ValueError("radius must be > 1 and <= size / 2")
        if self.sigma <= 0 or self.dt <= 0:
            raise ValueError("sigma and dt must be positive")
        if self.dtype not in {"float32", "float64"}:
            raise ValueError("dtype must be float32 or float64")


def kernel(config):
    coords = np.arange(config.size) - config.size // 2
    y, x = np.meshgrid(coords, coords, indexing="ij")
    r = np.hypot(x, y) / config.radius
    value = np.where(r < 1, (4 * r * (1 - r)) ** 4, 0)
    return (value / value.sum()).astype(config.dtype)


def initial_state(config, kind="orbium", seed=7):
    state = np.zeros((config.size, config.size), dtype=config.dtype)
    if kind == "orbium":
        if config.size < 20:
            raise ValueError("Orbium requires a grid of at least 20 x 20")
        lo = config.size // 2 - 10
        state[lo:lo + 20, lo:lo + 20] = ORBIUM
    elif kind == "random":
        coords = np.arange(config.size) - config.size // 2
        y, x = np.meshgrid(coords, coords, indexing="ij")
        state[:] = np.random.default_rng(seed).random(state.shape) * (np.hypot(x, y) < config.radius)
    else:
        raise ValueError(f"Unknown initial state: {kind}")
    return state


class NumpyLenia:
    def __init__(self, config=LeniaConfig()):
        self.config = config
        self.kernel = kernel(config)
        self.spectrum = np.fft.rfft2(np.fft.ifftshift(self.kernel))

    def potential(self, state):
        if state.shape[-2:] != (self.config.size, self.config.size):
            raise ValueError("state spatial shape does not match config")
        return np.fft.irfft2(np.fft.rfft2(state, axes=(-2, -1)) * self.spectrum,
                            s=state.shape[-2:], axes=(-2, -1))

    def step(self, state, intensity=0.0):
        c = self.config
        u = self.potential(state)
        growth = 2 * np.maximum(0, 1 - (u - c.mu) ** 2 / (9 * c.sigma ** 2)) ** 4 - 1
        return np.clip(state + c.dt * (growth - intensity * state), 0, 1).astype(c.dtype, copy=False)
