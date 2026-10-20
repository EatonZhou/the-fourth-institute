"""Thin regenerative-carrier adapter to the existing paid run/brake actor.

No alternative motor, payment, transport, or learning equations live here.
New births and template inheritance are owned by regenerative_actor.model.
advance is a REAL physical operation and must not be called for a preview.
"""
from dataclasses import asdict
import numpy as np
from local_learning.material_transport import Rule
from local_learning.adaptive_action_renewal.controller import (
    LearningRule, RunBrakeBody, NAMES, prepare, settle,
)


class ActorAdapter:
    def __init__(self, physical, learning):
        if physical.action_budget != 'donor_energy':
            raise ValueError('real old-donor funding required')
        if physical.energy_transfer_conductivity != 0:
            raise ValueError('no independent energy-transfer subsystem in this carrier')
        self.plant = RunBrakeBody(physical)
        self.learning = learning

    @classmethod
    def from_config(cls, config):
        return cls(Rule(**config['physical_rule']),LearningRule(**config['learning_rule']))

    def advance(self, old, controller, draw, cue, fuel, hazard, *, learn):
        if set(controller) != set(NAMES):
            raise ValueError('complete carried controller required')
        draw=np.asarray(draw)
        if draw.shape!=old.body.shape or not np.isfinite(draw).all() or not ((draw>=0)&(draw<1)).all():
            raise ValueError('one frozen uniform draw per grid cell required')
        dt=self.plant.rule.dt
        prepared,decision=prepare(controller,old.body,draw,dt,self.learning)
        self.plant.readout=prepared['run']
        after,metrics=self.plant.step(old,cue,fuel,hazard)
        settled,audit=settle(old.body,prepared,decision,after,metrics,dt,
                             self.plant.rule.uptake_rate,learn,self.learning)
        audit.update(run_readout=prepared['run'].copy(),
                     selection_probability=decision['probability'],
                     selected=decision['selected'],sampled=decision['sampled'],
                     score=decision['score'])
        return after,settled,metrics,audit
