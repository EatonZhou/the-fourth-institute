"""Build one reusable life0 and interface manifest; zero physical operations."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import numpy as np
from local_learning.material_transport import Rule
from local_learning.adaptive_action_renewal.controller import LearningRule, initial
from simulation.snapshot import load_snapshot, save_snapshot
from .conversion import Conversion

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'environment_evolution/results/developmental_ecologies_round1/batch2/opening-feedback-corridor--colocated/initial.npz'
AUTH = ROOT.parent/'knowledge_base/research/experience_learning/regenerative_actor_interface_20261017.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    temp.replace(path)


def prepare(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if (out/'contract.json').exists():
        raise FileExistsError('retain frozen interface contract; choose a fresh destination')
    parent = load_snapshot(SOURCE)
    if parent.step != 0 or parent.metadata['coupling'] != 0:
        raise ValueError('selected source must be life0 with no slow feedback')
    shape = parent.fields['body'].shape
    physical = Rule(dt=.05, action_budget='donor_energy', movement_cost=.08,
                    cue_gain=0., polarity_gain=.5, uptake_rate=.2,
                    energy_capacity=2., energy_transfer_conductivity=0.)
    config = dict(body_rule=parent.metadata['body_rule'], conversion=asdict(Conversion()),
                  physical_rule=asdict(physical), learning_rule=asdict(LearningRule()),
                  generation_enabled=True)
    rho = parent.fields['body'].copy()
    state = {k:parent.fields[k].copy() for k in
             ('body','energy','precursor','fuel','waste','heat','dead_energy','founder_mass')}
    state.update(memory=np.zeros((2,)+shape), eligibility=np.zeros((2,)+shape),
                 polarity=np.stack((np.zeros(shape), (rho>0).astype(float))),
                 **{'c_'+k:v for k,v in initial(shape).items()})
    state['kernels'] = np.stack((parent.fields['kernel1'], parent.fields['kernel2']))
    life0 = out/'initial.npz'
    save_snapshot(life0,state,step=0,metadata={'config':config,
        'source_initial':str(SOURCE),'source_sha256':sha(SOURCE),
        'initialization':'Source rho/E/P/F/kernels/rule unchanged. New p=(0,1) on tissue; controller/m/z neutral. No past experience copied.',
        'body_forward_steps':0,'energy_forward_steps':0})
    world = out/'world.npz'
    save_snapshot(world,{'cue':np.zeros((2,)+shape),'hazard':np.zeros(shape)},
                  step=0,metadata={'finite_supply':'P/F in initial state, static and not replenished',
                                   'cue':'zero; no geometry label enters actor'})
    files = [*Path(__file__).parent.glob('*.py'),
             ROOT/'local_learning/regenerative_actor_adapter.py',
             ROOT/'local_learning/material_transport.py',
             ROOT/'local_learning/adaptive_action_renewal/controller.py',
             ROOT/'morphology_evolution/dual_kernel.py',
             ROOT/'simulation/core.py', ROOT/'simulation/data.py',
             ROOT/'simulation/snapshot.py', ROOT/'simulation/__init__.py',
             SOURCE, AUTH, life0, world]
    if any(not p.is_file() for p in files):
        raise FileNotFoundError([str(p) for p in files if not p.is_file()])
    contract = dict(schema='lenia.regenerative-actor-interface.v1',
        status='implementation_only_no_physical_forward',owner='resident_ce2a5ab005bc',
        collaborator='resident_d3ccfa2d95b7',body_budget=0,steps=0,
        authorization=str(AUTH),initial=str(life0),world=str(world),
        future_runner='.venv/bin/python -m environment_evolution.regenerative_actor.runner --contract NEW_AUTHORIZED_MANIFEST',
        check_entry='.venv/bin/python -m environment_evolution.regenerative_actor.runner --contract '+str(out/'contract.json')+' --check',
        runtime={'cwd':str(ROOT),'python':str(ROOT/'.venv/bin/python'),'cpu_threads':2,'gpu':0,
                 'submission':'existing unified runtime runner, shared two CPU slots; no submission in this task'},
        config=config,sha256={str(p.resolve()):sha(p) for p in files},
        initial_totals={k:float(state[k].sum()) for k in ('body','energy','precursor','fuel','founder_mass')},
        order=['actor prepare','actual K / donor-funded motor / uptake / maintenance / injury-starvation',
               'actor settle on actual actor receipts','fixed dual-kernel generation response',
               'rule death and carried-E loss','capacity / donor-template support / P / F birth admission',
               'paid theta/p template inheritance; newborn trace/value/food/run/remaining/m/z zero'],
        units={'rho':'M/cell','P':'M/cell','E/F/heat/dead_energy':'EU/cell','dt':.05},
        conversion='1P + .42F -> 1 newborn M + .2E + .2 synthesis heat + .02 template heat',
        conservation={'material':'sum(rho+P+waste) constant under periodic transport',
                      'energy':'sum(E+F+heat+dead_energy) constant; no external replenishment',
                      'mass_change':'birth minus actor injury/starvation minus generation rule death',
                      'founder':'advect(old founder,K) * actor survival * rule survival; newborn founder=0'},
        inheritance={'support':'fixed periodic 3x3 [1,2,1] outer product /16 over rule survivors',
                     'templates':['theta','polarity'],
                     'donors':'mass-weighted current survivor concentrations; no donor mass duplication',
                     'empty_support':'birth denied, even with positive generating response and P/F',
                     'newborn_zero':['value','trace','food','run','remaining','memory','eligibility'],
                     'mixture':'survivor and newborn concentrations mix by actual admitted mass; unfinished traces diluted, not copied'},
        reward={'actor':'existing realized U-motor-basal-lost_E-.08*(actor injury+starvation)',
                'scope':'actor substep only; not total new-model ΔE+.08ΔM; not regeneration success',
                'whole_model':'retain generation birth/death/P/F/template costs and complete E/M changes separately'},
        degeneracies={'zero_birth':'no P or F or donor support gives zero newborn; old tissue still transports and dies',
                      'zero_action':'brake zeros active p readout only; morphology-driven K can remain',
                      'zero_K':'mobility=0 gives identity K and zero motor cost; uptake, maintenance and generation remain',
                      'overcapacity':'conservative K may compress rho>1; birth capacity is max(1-survivor,0), no mass clipping'},
        source_differences=['Only generation response/kernels/life0 borrowed; no old outcome transferred.',
            'Actor owns maintenance/uptake/starvation at dt=.05; not old budgeted-generation fee order.',
            'New p initialization and template support/fee are explicit new choices.',
            'No extra energy-only forward, no standalone communication cost beyond template fee.',
            'Fixed genome, lifetime actor parameters, tissue turnover are stored separately.'],
        research_status='No new body evaluated; interface readiness is not a scientific outcome',
        reserved_budgets='448/1024 untouched; do not consume assigned2048; first body batch needs independent authorization')
    write_json(out/'contract.json',contract)
    return contract


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=prepare(a.out)
    print(json.dumps({'contract':str(a.out/'contract.json'),'body_steps':0,'energy_steps':0,'initial':result['initial_totals']}))


if __name__=='__main__':main()
