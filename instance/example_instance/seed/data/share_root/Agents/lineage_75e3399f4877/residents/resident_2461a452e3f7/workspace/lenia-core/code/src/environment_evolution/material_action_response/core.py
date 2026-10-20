"""Native generation, local donor-paid face transport; no centroid actuator.

The two action channels replace the old run-scaled polarity force only.
Inherited polarity and old controller fields remain carried physical contents,
but do not choose this extension's action. Original source files are immutable.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import numpy as np
from local_learning.material_transport import Rule, MaterialBody, advect, gradient
from environment_evolution.regenerative_actor.model import (
    RegenerativeBody, ForwardPermit, PHYSICAL, STOCKS, CONTROLLER)
from simulation.snapshot import save_snapshot, load_snapshot

SCHEMA = 'material-action-response-v1'
DIRECTIONS = ((1, -2), (-1, -2), (1, -1), (-1, -1))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def geometry(size=96, variant='development'):
    """Static public world; alternate geometry available for held-out study.

    Inherited stocks are NOT replaced. A finite horizon bounds external input.
    Permeability is symmetric on each shared face, without a transport sink.
    """
    y, x = np.indices((size, size)); c = size/2
    patch = np.maximum(np.exp(-((x-c-14)**2+(y-c)**2)/32),
                       np.exp(-((x-c)**2+(y-c+16)**2)/32))
    material = np.ones((size, size))
    material[(abs(x-c-8) <= 1) & (abs(y-c) > 8)] = .2
    if variant == 'heldout_rotated':
        patch = np.rot90(patch).copy(); material = np.rot90(material).copy()
    elif variant != 'development':
        raise ValueError('unknown geometry')
    faces = np.stack([np.minimum(material, np.roll(material, -d, axis=a))
                      for d, a in DIRECTIONS])
    return dict(face_permeability=faces, fuel_input=.004*patch,
                precursor_input=.001*patch, hazard=np.zeros_like(patch))


class VectorPlant(MaterialBody):
    def face_rates(self, state, cue):
        # Reuse exactly the original passive/cue potential, saturation and gain.
        # Proxy p is an actuator request, not a rewrite of the carried p field.
        rates = super().face_rates(replace(state, polarity=self.action), cue)
        self.raw_rates = rates
        self.blocked_rates = rates*self.permeability
        return self.blocked_rates


class VectorAdapter:
    def __init__(self, config, faces):
        self.plant = VectorPlant(Rule(**config['physical_rule']))
        self.plant.permeability = faces
        self.plant.action = np.zeros((2,)+faces.shape[1:])

    def advance(self, old, controller, draw, cue, fuel, hazard, *, learn):
        if learn:
            raise ValueError('external response learner owns updates; native actor disabled')
        after, metrics = self.plant.step(old, cue, fuel, hazard, plastic=False)
        w = metrics['transport_weights']; transported = advect(old.body, w)
        def carry(value):
            return np.divide(advect(old.body*value, w), transported,
                             out=np.zeros_like(value), where=transported > 0)
        settled = {k:carry(v) for k,v in controller.items()}
        for v in settled.values():
            v[..., after.body == 0] = 0
        raw = self.plant.rule.dt*self.plant.blocked_rates
        cfl = np.minimum(1., .8/np.maximum(raw.sum(0), 1e-300))
        proposed = raw*cfl
        metrics.update(action_requested=self.plant.action.copy(),
                       face_rate_unblocked=self.plant.raw_rates.copy(),
                       weights_before_payment=proposed,
                       flux_requested=old.body*proposed,
                       flux_executed=old.body*w,
                       body_after_actor=after.body.copy(),
                       energy_after_actor=after.energy.copy())
        reward = (metrics['resource_consumed']-metrics['energy_spent']
                  -metrics['energy_lost_with_tissue'])
        return after, settled, metrics, dict(full_reward=reward,
                                             transported_body=transported)


class MaterialActionBody:
    def __init__(self, config, kernels, state, *, world=None, source=None,
                 step=128, elapsed=0, supply_steps=256, input_material=0., input_energy=0.):
        self.world = deepcopy(world if world is not None else geometry(state['body'].shape[0]))
        self.adapter = VectorAdapter(config, self.world['face_permeability'])
        self.model = RegenerativeBody(config, kernels, self.adapter)
        self.state = {k:v.copy() for k,v in state.items()}
        self.model.validate(self.state)
        self.source = source; self.clock = step; self.elapsed = elapsed
        self.supply_steps = supply_steps
        self.input_material = input_material; self.input_energy = input_energy

    @classmethod
    def from_formation(cls, path, expected_sha, **kwargs):
        if sha(path) != expected_sha:
            raise ValueError('source packet SHA mismatch')
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(str(z['metadata_json']))
            if meta['step'] != 128 or meta['transport_allocation']['phase'] != 'ready':
                raise ValueError('need actual complete ready 128 state')
            state = {k:z['state__'+k].copy() for k in PHYSICAL+STOCKS+tuple('c_'+k for k in CONTROLLER)}
            kernels = z['state__kernels'].copy()
        return cls(meta['config'], kernels, state,
                   source=dict(path=str(path), sha256=expected_sha, metadata=meta), **kwargs)

    def observe_local(self):
        # Whole arrays represent one local value per cell, not permission to
        # aggregate globally. A learner may read the four-neighbour stencil.
        return {**{k:self.state[k].copy() for k in ('body','energy','fuel','precursor','polarity')},
                'face_permeability':self.world['face_permeability'].copy(),
                'hazard':self.world['hazard'].copy()}

    def step(self, action, *, permit):
        a = np.asarray(action, dtype=np.float64)
        if a.shape != (2,)+self.state['body'].shape or not np.isfinite(a).all():
            raise ValueError('finite action (y,x,H,W represented as 2,H,W) required')
        if np.max(np.hypot(a[0], a[1])) > 1+1e-12:
            raise ValueError('local vector norm exceeds one')
        self.model.validate(self.state)
        old = self.state
        supplied = {k:v.copy() for k,v in old.items()}
        enabled = self.elapsed < self.supply_steps
        ip = self.world['precursor_input'] if enabled else np.zeros_like(old['body'])
        ie = self.world['fuel_input'] if enabled else np.zeros_like(old['body'])
        supplied['precursor'] += ip; supplied['fuel'] += ie
        self.adapter.plant.action = a.copy()
        nxt, maps, checks = self.model.step(supplied, np.zeros_like(old['body']),
            np.zeros((2,)+old['body'].shape), self.world['hazard'], learn=False, permit=permit)
        w = maps['actor_transport_weights']; transported = advect(old['body'], w)
        survivor = maps['actor_body_after_actor']-maps['rule_death']
        keep = np.divide(survivor, transported, out=np.zeros_like(survivor), where=transported>0)
        maps.update(old_body=old['body'].copy(), old_energy=old['energy'].copy(),
                    old_fuel=old['fuel'].copy(), old_precursor=old['precursor'].copy(),
                    transported_body=transported, survivor=survivor, keep=keep,
                    born=maps['births'], death=transported-survivor,
                    uptake=maps['actor_resource_consumed'], energy=nxt['energy'].copy(),
                    K_weights=w, K_stay=1-w.sum(0),
                    input_material=ip.copy(), input_energy=ie.copy(),
                    completed_mask=transported>0)
        checks.update(reconstruction_error=float(abs(nxt['body']-survivor-maps['births']).max()),
                      K_column_error=float(abs(maps['K_stay']+w.sum(0)-1).max()),
                      transport_error=float(abs(transported.sum()-old['body'].sum())))
        self.state = nxt; self.clock += 1; self.elapsed += 1
        self.input_material += float(ip.sum()); self.input_energy += float(ie.sum())
        return maps, checks

    def save(self, path):
        save_snapshot(path, {**self.state, 'kernels':self.model.kernels,
            **{'world_'+k:v for k,v in self.world.items()}}, step=self.clock,
            metadata=dict(schema=SCHEMA, config=self.model.config, source=self.source,
                          elapsed=self.elapsed, supply_steps=self.supply_steps,
                          input_material=self.input_material, input_energy=self.input_energy))

    @classmethod
    def restore(cls, path):
        snap = load_snapshot(path); m = snap.metadata
        if m['schema'] != SCHEMA: raise ValueError('wrong snapshot schema')
        return cls(m['config'], snap.fields['kernels'],
            {k:v for k,v in snap.fields.items() if not k.startswith('world_') and k!='kernels'},
            world={k[6:]:v for k,v in snap.fields.items() if k.startswith('world_')},
            source=m['source'], step=snap.step, elapsed=m['elapsed'], supply_steps=m['supply_steps'],
            input_material=m['input_material'], input_energy=m['input_energy'])


def fixed_action(observation, mode):
    body=observation['body']; y,x=np.indices(body.shape); c=(body.shape[0]-1)/2
    if mode == 'none': return np.zeros((2,)+body.shape)
    if mode == 'gradient':
        action=np.stack(gradient(observation['fuel']))
        norm=np.hypot(action[0],action[1])
        return action/np.maximum(norm, .02)
    if mode == 'shear_x': return np.stack((np.zeros_like(body),np.tanh((y-c)/4)))
    if mode == 'shear_y': return np.stack((np.tanh((x-c)/4),np.zeros_like(body)))
    raise ValueError(mode)
