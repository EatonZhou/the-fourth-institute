"""Physical integration entry. DO NOT call step without a new funded job.

The actor adapter owns actual transport/payment and local policy settlement.
This module owns fixed generation, material turnover and complete conservation.
"""
from copy import deepcopy
from dataclasses import asdict
import os
import numpy as np
from morphology_evolution.dual_kernel import DualKernel
from local_learning.material_transport import State, advect
from simulation.snapshot import load_snapshot, save_snapshot
from .conversion import Conversion, admit, divide, template, neighborhood

PHYSICAL = ('body', 'energy', 'memory', 'eligibility', 'polarity')
STOCKS = ('precursor', 'fuel', 'waste', 'heat', 'dead_energy', 'founder_mass')
CONTROLLER = ('theta', 'value', 'trace', 'food', 'run', 'remaining')


class ForwardPermit:
    """Count attempted complete steps before any physical operation.

    This is an accounting guard, not a substitute for PI authorization/control.
    Runner checks the independently issued manifest and current research control.
    """
    def __init__(self, maximum, authorization):
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise ValueError('new positive body budget required; current interface cap is zero')
        if not authorization or not os.environ.get('LENIA_JOB_ID'):
            raise RuntimeError('independent authorization and unified runner required')
        self.maximum, self.authorization, self.attempted = maximum, authorization, 0

    def consume(self):
        if self.attempted >= self.maximum:
            raise RuntimeError('independent body budget exhausted')
        self.attempted += 1


class RegenerativeBody:
    def __init__(self, config, kernels, actor):
        self.config = deepcopy(config)
        self.kernels = np.asarray(kernels).copy()
        self.engine = DualKernel(config['body_rule'], self.kernels)
        self.dt = self.engine.engines[0].config.dt
        self.conversion = Conversion(**config['conversion'])
        self.actor = actor
        if tuple(self.kernels.shape) != (2, self.engine.engines[0].config.size,
                                         self.engine.engines[0].config.size):
            raise ValueError('two kernels on the body grid required')
        if config['physical_rule']['dt'] != self.dt:
            raise ValueError('one physical/generation clock required')
        if config['physical_rule']['energy_capacity'] < self.conversion.endowment:
            raise ValueError('newborn energy exceeds actor energy capacity')
        if config['physical_rule']['energy_transfer_conductivity'] != 0:
            raise ValueError('this isolated carrier has no separate energy-transfer operation')

    def rate(self, body):
        a, b = self.engine.engines
        w = self.engine.weight
        return ((1-w)*self.engine.response(a.potential(body), a.config)
                + w*self.engine.response(b.potential(body), b.config))

    def validate(self, s):
        size = self.engine.engines[0].config.size
        shape = (size, size)
        for key in PHYSICAL+STOCKS+tuple('c_'+k for k in CONTROLLER):
            value = s[key]
            channels = key in ('memory', 'eligibility', 'polarity', 'c_theta', 'c_value', 'c_trace')
            if value.shape != ((2,)+shape if channels else shape) or not np.isfinite(value).all():
                raise ValueError('invalid field '+key)
        for key in ('body', 'energy')+STOCKS:
            if s[key].min() < -1e-12:
                raise ValueError('negative field '+key)
        if np.max(s['founder_mass']-s['body']) > 1e-11:
            raise ValueError('founder marker exceeds tissue')
        if np.max(s['energy']-self.config['physical_rule']['energy_capacity']*s['body']) > 1e-11:
            raise ValueError('local energy capacity exceeded')
        # Transport is conservative and may compress above density_capacity.
        # The threshold restricts new births only; do not clip existing mass.

    def step(self, s, draw, cue, hazard, *, learn, permit):
        if not isinstance(permit, ForwardPermit):
            raise RuntimeError('explicit newly funded ForwardPermit required')
        self.validate(s)
        permit.consume()  # failures after this point count; no silent retries
        old = State(*(s[k] for k in PHYSICAL))
        c = {k:s['c_'+k] for k in CONTROLLER}
        after, settled, metrics, audit = self.actor.advance(
            old, c, draw, cue, s['fuel'], hazard, learn=learn)
        w = metrics['transport_weights']
        transported = advect(s['body'], w)
        retention = divide(after.body, transported)
        founder = advect(s['founder_mass'], w)*retention
        fuel = s['fuel']-metrics['resource_consumed']
        h = self.rate(after.body)
        rule_death = np.minimum(after.body, self.dt*np.maximum(-h, 0))
        survivor = after.body-rule_death
        survive_fraction = divide(survivor, after.body)
        death_energy = after.energy*(1-survive_fraction)
        own_energy = after.energy-death_energy
        support = neighborhood(survivor)
        request = self.dt*np.maximum(h, 0) if self.config['generation_enabled'] else np.zeros_like(h)
        conversion = admit(request, survivor, s['precursor'], fuel, support, self.conversion)
        born = conversion['births']
        total = survivor+born
        # Only theta and true polarity are newborn templates. Other fields
        # retain survivors' extensive content, with zero newborn contribution.
        inherited_theta, _ = template(survivor, settled['theta'])
        inherited_p, _ = template(survivor, after.polarity)
        newcontroller = {k:divide(survivor*settled[k], total) for k in CONTROLLER}
        newcontroller['theta'] = divide(survivor*settled['theta']+born*inherited_theta, total)
        nxt = dict(body=total,
                   energy=own_energy+conversion['newborn_energy'],
                   memory=divide(survivor*after.memory, total),
                   eligibility=divide(survivor*after.eligibility, total),
                   polarity=divide(survivor*after.polarity+born*inherited_p, total),
                   precursor=s['precursor']-born,
                   fuel=fuel-conversion['birth_fuel'],
                   waste=s['waste']+metrics['mass_injury']+metrics['mass_starvation']+rule_death,
                   heat=s['heat']+metrics['energy_spent']+conversion['synthesis_heat']+conversion['template_heat'],
                   dead_energy=s['dead_energy']+metrics['energy_lost_with_tissue']+death_energy,
                   founder_mass=founder*survive_fraction,
                   **{'c_'+k:v for k,v in newcontroller.items()})
        self.validate(nxt)
        maps = {**{'actor_'+k:v for k,v in metrics.items()},
                **{'controller_'+k:v for k,v in audit.items()},
                **conversion, 'growth_rate':h, 'rule_death':rule_death,
                'rule_death_energy':death_energy, 'template_support':support,
                'inherited_theta':inherited_theta, 'inherited_polarity':inherited_p,
                'actor_reward':audit['full_reward'],
                'founder_surviving':nxt['founder_mass'],
                'new_tissue_surviving':nxt['body']-nxt['founder_mass']}
        mat = lambda x: sum(float(x[k].sum()) for k in ('body', 'precursor', 'waste'))
        energy = lambda x: sum(float(x[k].sum()) for k in ('energy', 'fuel', 'heat', 'dead_energy'))
        checks = dict(material_error=mat(nxt)-mat(s), energy_error=energy(nxt)-energy(s),
                      max_density=float(total.max()), attempted_body_steps=permit.attempted)
        if max(abs(checks['material_error']),abs(checks['energy_error'])) > 1e-9:
            raise ArithmeticError(checks)
        # Retain actor's narrow reward separately from whole-body generation.
        maps['whole_energy_change'] = nxt['energy']-s['energy']
        maps['whole_mass_change'] = nxt['body']-s['body']
        return nxt, maps, checks

    def save(self, path, s, step, metadata=None):
        self.validate(s)
        save_snapshot(path, {**s, 'kernels':self.kernels}, step=step,
                      metadata={**(metadata or {}), 'config':self.config,
                                'model':'environment_evolution.regenerative_actor'})


def load(path, actor_factory):
    snap = load_snapshot(path)
    config = snap.metadata['config']
    model = RegenerativeBody(config, snap.fields['kernels'], actor_factory(config))
    state = {k:v.copy() for k,v in snap.fields.items() if k != 'kernels'}
    model.validate(state)
    return model, state, snap
