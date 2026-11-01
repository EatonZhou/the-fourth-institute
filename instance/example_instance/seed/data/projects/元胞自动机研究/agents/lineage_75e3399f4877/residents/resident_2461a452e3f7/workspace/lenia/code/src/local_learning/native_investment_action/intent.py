"""An initial local vector carried by actual material, without amplitude repair.

This module never constructs or advances a physical body. It is not a learning
rule. Death removes intent; newborn material receives none. Signed vectors can
cancel when material mixes. Returned bookkeeping is not feedback to a learner.
"""
from copy import deepcopy
import numpy as np
from local_learning.material_transport import advect

SCHEMA = 'material-investment-intent-v1'
TOL = 1e-10


def _array(value, shape, name, *, nonnegative=False):
    a = np.asarray(value, dtype=np.float64)
    if a.shape != shape or not np.isfinite(a).all():
        raise ValueError(name + ': shape or nonfinite value')
    if nonnegative and np.any(a < 0):
        raise ValueError(name + ': negative value')
    return a


def _same(a, b, name):
    if not np.allclose(a, b, rtol=1e-12, atol=TOL):
        raise ValueError(name + ': mismatched actual receipt/state')


class MaterialIntent:
    """Register once, then use one advance per completed physical step.

    `readout` returns requested (dy,dx), not paid velocity or actual flux.
    `clock` is the body's absolute post-step clock; duplicates/gaps are rejected.
    The initial vector is supplied by the caller, e.g. frozen host shear_x.
    """
    def __init__(self, body, initial_action, *, clock):
        body = np.asarray(body, dtype=np.float64)
        if body.ndim != 2 or min(body.shape) == 0:
            raise ValueError('body must be a nonempty two-dimensional field')
        _array(body, body.shape, 'body', nonnegative=True)
        action = _array(initial_action, (2, *body.shape), 'initial_action')
        if np.any(np.hypot(action[0], action[1]) > 1 + TOL):
            raise ValueError('initial action norm exceeds one')
        if int(clock) != clock:
            raise ValueError('integer clock required')
        self.body = body.copy()
        self.cohort = body.copy()
        self.extensive = action * body
        self.initial_mass = float(body.sum())
        self.initial_intent = self.extensive.sum(axis=(-2, -1))
        self.dead_mass = 0.
        self.removed_intent = np.zeros(2)
        self.start_clock = self.clock = int(clock)
        self.steps = 0

    def readout(self, body=None):
        """Divide carried intent by final total rho, including newborn dilution.

        No clipping, unit normalization, rescaling, or fresh intent is applied.
        During withdrawal the caller requests zeros but still advances this
        passive state if it wants the full original-material accounting.
        """
        rho = self.body if body is None else _array(body, self.body.shape, 'body', nonnegative=True)
        _same(rho, self.body, 'readout body')
        return np.divide(self.extensive, rho, out=np.zeros_like(self.extensive), where=rho > 0)

    def advance(self, receipt, *, final_body, clock):
        """Consume actual K, destination keep and births; never infer a new K.

        Required keys: old_body, K_weights, transported_body, survivor, keep,
        born. If K_stay exists, its equality with 1-sum(K_weights) is checked.
        Optional actual uptake yields descriptive initial-cohort contact BEFORE
        death. Missing uptake remains unknown, never a fabricated zero label.
        Validation finishes before this object is mutated.
        """
        if int(clock) != clock or clock != self.clock + 1:
            raise ValueError('nonconsecutive/replayed physical receipt')
        shape = self.body.shape
        before = _array(receipt['old_body'], shape, 'old_body', nonnegative=True)
        w = _array(receipt['K_weights'], (4, *shape), 'K_weights', nonnegative=True)
        transported = _array(receipt['transported_body'], shape, 'transported_body', nonnegative=True)
        survivor = _array(receipt['survivor'], shape, 'survivor', nonnegative=True)
        keep = _array(receipt['keep'], shape, 'keep', nonnegative=True)
        born = _array(receipt['born'], shape, 'born', nonnegative=True)
        final = _array(final_body, shape, 'final_body', nonnegative=True)
        _same(before, self.body, 'old body')
        if np.any(w.sum(0) > 1) or np.any(keep > 1):
            raise ValueError('actual K/keep outside conservative range')
        if 'K_stay' in receipt:
            stay = _array(receipt['K_stay'], shape, 'K_stay', nonnegative=True)
            _same(stay, 1-w.sum(0), 'K_stay')
        _same(advect(before, w), transported, 'transported body')
        _same(transported * keep, survivor, 'old survivor')
        if np.any((transported == 0) & (survivor != 0)):
            raise ValueError('survivor without transported old material')
        _same(survivor + born, final, 'final body/birth separation')
        moved_cohort = advect(self.cohort, w)
        moved_intent = advect(self.extensive, w)
        deleted_cohort = moved_cohort * (1-keep)
        deleted_intent = moved_intent * (1-keep)
        cohort = moved_cohort * keep
        extensive = moved_intent * keep
        if np.any(cohort > survivor + TOL) or np.any(np.hypot(*extensive) > cohort + TOL):
            raise ValueError('carried intent exceeds its actual surviving material')
        # This is descriptive mass-share attribution, not measured causal energy.
        contact = None
        unknown_contact = float(moved_cohort.sum())
        if 'uptake' in receipt:
            uptake = _array(receipt['uptake'], shape, 'uptake', nonnegative=True)
            fraction = np.divide(moved_cohort, transported, out=np.zeros_like(cohort), where=transported > 0)
            contact = uptake * fraction
            unknown_contact = 0.
        dead_mass = self.dead_mass + float(deleted_cohort.sum())
        removed_intent = self.removed_intent + deleted_intent.sum(axis=(-2, -1))
        report = dict(clock=int(clock), registered_initial_mass=self.initial_mass,
                      cohort_before=self.cohort.copy(), cohort_transported=moved_cohort,
                      cohort_survivor=cohort.copy(), cohort_death=deleted_cohort,
                      intent_transported=moved_intent, intent_deleted=deleted_intent,
                      cohort_uptake_before_death=contact,
                      unknown_contact_mass=unknown_contact,
                      cumulative_terminal_death=dead_mass,
                      registration_balance_error=float(cohort.sum()+dead_mass-self.initial_mass),
                      signed_intent_balance_error=extensive.sum(axis=(-2,-1))+removed_intent-self.initial_intent)
        self.body = final.copy()
        self.cohort = cohort
        self.extensive = extensive
        self.dead_mass = dead_mass
        self.removed_intent = removed_intent
        self.clock = int(clock)
        self.steps += 1
        return report

    def checkpoint(self):
        return deepcopy(dict(schema=SCHEMA, body=self.body, cohort=self.cohort,
             extensive=self.extensive, initial_mass=self.initial_mass,
             initial_intent=self.initial_intent, dead_mass=self.dead_mass,
             removed_intent=self.removed_intent, start_clock=self.start_clock,
             clock=self.clock, steps=self.steps))

    @classmethod
    def restore(cls, checkpoint):
        state = deepcopy(checkpoint)
        if state.pop('schema', None) != SCHEMA:
            raise ValueError('wrong material intent checkpoint schema')
        obj = cls(state['body'], np.zeros_like(state['extensive']), clock=state['clock'])
        shape = obj.body.shape
        for key in ('cohort',):
            _array(state[key], shape, key, nonnegative=True)
        _array(state['extensive'], (2, *shape), 'extensive')
        for key in ('initial_intent','removed_intent'):
            _array(state[key], (2,), key)
        if not np.isfinite([state['initial_mass'],state['dead_mass']]).all() or min(state['initial_mass'],state['dead_mass']) < 0:
            raise ValueError('invalid registration ledger')
        if state['steps'] < 0 or state['clock']-state['start_clock'] != state['steps']:
            raise ValueError('invalid clock ledger')
        if np.any(state['cohort'] > state['body']+TOL) or np.any(np.hypot(*state['extensive']) > state['cohort']+TOL):
            raise ValueError('checkpoint carries intent without registered material')
        _same(np.asarray(state['cohort'].sum()+state['dead_mass']), np.asarray(state['initial_mass']), 'registered mass')
        _same(state['extensive'].sum(axis=(-2,-1))+state['removed_intent'],state['initial_intent'],'signed intent')
        obj.__dict__.update(state)
        return obj
