import copy
import numpy as np
from local_learning.material_transport import advect


def divide(a, b):
    return np.divide(a, b, out=np.zeros_like(a), where=b > 0)


class RecentRecords:
    """Discount once per COMPLETED unit, never once per live direct snapshot.

    Each bin stores received descriptive numerator and exposure, separately for
    own and message records. These are not independent samples or counterfactuals.
    They subsequently move with the actual recipient material and die with it.
    """
    def __init__(self, shape, seed):
        self.direct = np.zeros((4, 2, *shape))
        self.message = np.zeros_like(self.direct)
        self.last_clock = -1
        self.rng = np.random.Generator(np.random.PCG64(seed))

    def transport(self, maps):
        for name in ('direct', 'message'):
            setattr(self, name, advect(getattr(self, name), maps['K_weights']) * maps['keep'])

    def update(self, feedback, survivor, arm):
        if feedback is None:
            return False
        clock = feedback['source_clock']
        if clock <= self.last_clock:
            raise ValueError('duplicate or reversed completion')
        self.last_clock = clock
        if arm == 'frozen':
            return False
        for name, key in [('direct', 'direct'), ('message', 'message_unread')]:
            if name == 'message' and arm == 'ignore':
                continue
            r = feedback[key]
            # Only actual received records in surviving OLD recipients are read.
            valid = (r[:, 0] > 0) & (r[:, 6] > 0) & (survivor > 0)
            observation = np.stack((r[:, 1]-r[:, 2]-r[:, 3], r[:, 6]), axis=1)
            old = getattr(self, name)
            setattr(self, name, np.where(valid[:, None], .5*old + observation, old))
        return True

    def predict(self, ignore=False):
        s = self.direct if ignore else self.direct + self.message
        known = s[:, 1] > 1e-12
        score = divide(s[:, 0], s[:, 1])
        return score, known

    def choose(self, body, arm):
        if arm == 'prefix':
            codes = self.rng.integers(0, 4, (12, 12)).repeat(8, 0).repeat(8, 1)
            return codes, None
        if arm in ('euler_unit', 'euler'):
            return np.full(body.shape, 2, int), None
        if arm == 'none':
            return np.zeros(body.shape, int), None
        score, known = self.predict(ignore=arm == 'ignore')
        codes = np.argmax(np.where(known, score, -np.inf), axis=0)
        codes = np.where(known.any(0) & (body > 0), codes, 0)
        return codes, dict(score=score, known=known)

    def checkpoint(self):
        return dict(direct=self.direct.copy(), message=self.message.copy(),
                    last_clock=self.last_clock, rng=copy.deepcopy(self.rng.bit_generator.state))

    @classmethod
    def restore(cls, state):
        obj = cls(state['direct'].shape[-2:], 0)
        obj.direct = state['direct'].copy(); obj.message = state['message'].copy()
        obj.last_clock = state['last_clock']
        obj.rng.bit_generator.state = copy.deepcopy(state['rng'])
        return obj
