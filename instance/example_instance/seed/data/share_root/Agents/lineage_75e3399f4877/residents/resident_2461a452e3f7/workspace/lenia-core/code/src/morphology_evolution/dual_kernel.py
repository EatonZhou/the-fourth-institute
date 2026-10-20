"""Fixed two-potential Lenia rule; body is the only changing field.

Spatial convolution and the closed single-kernel baselines use NumpyLenia.
Two independent polynomial responses are mixed BEFORE the single clip.
This is an experimental rule family, not a claim of a new learning mechanism.
"""
from dataclasses import asdict
import numpy as np
from simulation import LeniaConfig, NumpyLenia, load_snapshot, save_snapshot


class DualKernel:
    def __init__(self, rule, kernels=None):
        self.rule = rule
        self.engines = [NumpyLenia(LeniaConfig(**c)) for c in rule['configs']]
        a, b = self.engines
        assert a.config.size == b.config.size and a.config.dt == b.config.dt
        self.weight = float(rule['weight_second'])
        if not 0 <= self.weight <= 1:
            raise ValueError('weight outside [0,1]')
        if kernels is not None:
            for engine, k in zip(self.engines, kernels):
                if k.shape != engine.kernel.shape or not np.isfinite(k).all() or np.min(k) < 0 or abs(k.sum()-1) > 1e-12:
                    raise ValueError('invalid explicit kernel')
                engine.kernel = k.copy()
                engine.spectrum = np.fft.rfft2(np.fft.ifftshift(k))

    @staticmethod
    def response(potential, config):
        return 2*np.maximum(0, 1-(potential-config.mu)**2/(9*config.sigma**2))**4-1

    def step(self, body, intensity=0.):
        a, b = self.engines
        if self.weight == 0:
            return a.step(body, intensity)
        if self.weight == 1:
            return b.step(body, intensity)
        g = ((1-self.weight)*self.response(a.potential(body), a.config)
             + self.weight*self.response(b.potential(body), b.config))
        return np.clip(body+a.config.dt*(g-intensity*body), 0, 1)

    def save(self, path, body, step, **metadata):
        save_snapshot(path, {'body': body, 'kernel1': self.engines[0].kernel,
                             'kernel2': self.engines[1].kernel}, step=step,
                      metadata={'rule': self.rule, 'dynamic_fields': ['body'],
                                'static_fields': ['kernel1', 'kernel2'], **metadata})


def load_candidate(path):
    s = load_snapshot(path, required_fields=('body', 'kernel1', 'kernel2'))
    engine = DualKernel(s.metadata['rule'], [s.fields['kernel1'], s.fields['kernel2']])
    return engine, s.fields['body'], s.step, s.metadata


def rule_spec(first, second, weight):
    return {'schema': 'lenia.fixed-dual-response.v1', 'configs': [asdict(first), asdict(second)],
            'weight_second': float(weight), 'kernel_definition': 'shared normalized polynomial radial kernel',
            'growth_definition': 'G_i(u)=2*max(0,1-(u-mu_i)^2/(9*sigma_i^2))^4-1',
            'update': 'clip(A+dt*((1-w)*G1(K1*A)+w*G2(K2*A)-intensity*A),0,1)',
            'time_rule': 'fixed dt=0.1; fixed lifetime kernels, responses and weight; no internal memory'}
