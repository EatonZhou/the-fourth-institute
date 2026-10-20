"""Unified runner worker. Current cap-zero manifest supports --check only.

Each future transition is journaled BEFORE physics; ambiguous attempts refuse
automatic replay. Complete states and all receipt maps are saved every step.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import numpy as np
from simulation.snapshot import load_snapshot, save_snapshot
from .model import load, ForwardPermit
from .prepare import sha, write_json

STOP=False


def halt(*_):
    global STOP
    STOP=True


def control():
    client='${code_root}/scripts/resident_hatg_instruction.py'
    values={}
    for key in ('research.desired_state','research.started'):
        proc=subprocess.run(['python3',client,'--resident-id','resident_ce2a5ab005bc',
              'call','GetSharedState','--arguments-json',json.dumps({'lineage_id':'lineage_75e3399f4877','key':key})],
              capture_output=True,text=True,check=True)
        envelope=json.loads(proc.stdout);r=envelope.get('result',envelope)
        values[key]=r.get('value',r.get('state',{}).get(key))
    if values['research.desired_state']!='running' or values['research.started'] is not True:
        raise RuntimeError('research control does not permit new physics: '+str(values))
    return values


def check(plan):
    for name, expected in plan['sha256'].items():
        if sha(name)!=expected:
            raise RuntimeError('frozen input/source changed: '+name)
    from local_learning.regenerative_actor_adapter import ActorAdapter
    model,state,snap=load(plan['initial'],ActorAdapter.from_config)
    if snap.step!=0:raise ValueError('this first carrier runner starts only at life0')
    world=load_snapshot(plan['world'])
    for name in ('cue','hazard'):
        if name not in world.fields:raise ValueError('missing world field '+name)
    return model,state,world.fields


def run(plan,contract_path):
    maximum=plan.get('body_budget',0);steps=plan.get('steps',0)
    if not isinstance(steps,int) or not 0<steps<=maximum:
        raise RuntimeError('interface manifest authorizes ZERO forward; a new independently funded plan is required')
    authorization=Path(plan['authorization'])
    auth=json.loads(authorization.read_text())
    if auth.get('body_forward_cap',0)<maximum:
        raise RuntimeError('independent authorization cap missing or insufficient')
    if str(authorization.resolve()) not in plan['sha256']:
        raise RuntimeError('freeze independent authorization digest')
    if not os.environ.get('LENIA_RUN_DIR') or not os.environ.get('LENIA_JOB_ID'):
        raise RuntimeError('unified runner required')
    model,state,world=check(plan)
    tape_path=Path(plan['tape'])
    if str(tape_path.resolve()) not in plan['sha256']:raise ValueError('freeze random tape')
    tape=np.load(tape_path,allow_pickle=False)
    if tape.shape!=(steps,*state['body'].shape) or not ((tape>=0)&(tape<1)).all():
        raise ValueError('invalid frozen actor draws')
    output=Path(os.environ['LENIA_RUN_DIR']);transactions=output/'transactions'
    transactions.mkdir(exist_ok=True)
    if any(transactions.iterdir()) or (output/'pending.json').exists():
        raise RuntimeError('existing attempts: audit/resume design required; do not replay')
    control()
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
        if int(os.environ.get(name,'2'))>2:raise RuntimeError('at most two CPU threads required')
    permit=ForwardPermit(maximum,str(authorization))
    signal.signal(signal.SIGTERM,halt);signal.signal(signal.SIGINT,halt)
    write_json(output/'contract.json',plan)
    committed=0
    try:
        for index in range(steps):
            if STOP:raise InterruptedError('cooperative stop before next attempted body step')
            if index and index%32==0:control()
            write_json(output/'pending.json',{'step':index+1,'phase':'before_physics',
                        'attempted_including_pending':index+1,'contract_sha256':sha(contract_path)})
            state,maps,checks=model.step(state,tape[index],world['cue'],world['hazard'],
                                        learn=bool(plan.get('learn',True)),permit=permit)
            model.save(transactions/f'{index+1:06d}.npz',state,index+1,
                       {'attempted':permit.attempted,'job_id':os.environ['LENIA_JOB_ID']})
            save_snapshot(transactions/f'{index+1:06d}-receipts.npz',
                          {k:np.asarray(v) for k,v in maps.items()},step=index+1,metadata=checks)
            row={k:float(np.asarray(v).sum()) for k,v in maps.items() if np.asarray(v).ndim<=2}
            row.update({k:float(state[k].sum()) for k in ('body','energy','precursor','fuel','waste','heat','dead_energy','founder_mass')})
            row.update(step=index+1,**checks)
            write_json(transactions/f'{index+1:06d}-ledger.json',row)
            committed=index+1
            write_json(output/'progress.json',{'attempted':permit.attempted,'committed':committed,
                       'M':float(state['body'].sum()),'E':float(state['energy'].sum())})
            (output/'pending.json').unlink()
        model.save(output/'final.npz',state,steps)
    finally:
        write_json(output/'result.json',{'status':'completed' if committed==steps else 'incomplete',
                   'attempted_body_steps':permit.attempted,'committed_body_steps':committed,
                   'independent_energy_steps':0,'job_id':os.environ['LENIA_JOB_ID']})


def main():
    p=argparse.ArgumentParser();p.add_argument('--contract',type=Path,required=True);p.add_argument('--check',action='store_true')
    a=p.parse_args();plan=json.loads(a.contract.read_text())
    if a.check:
        _,state,_=check(plan)
        print(json.dumps({'status':'interface_valid','body_steps':0,'energy_steps':0,
                          'M0':float(state['body'].sum()),'E0':float(state['energy'].sum())}))
    else:run(plan,a.contract)


if __name__=='__main__':main()
