"""One serial bounded job. Preparation and analysis never advance a body."""
import csv
import json
import os
from pathlib import Path
import signal
import time
import zipfile
import numpy as np
from environment_evolution.material_action_response.core import MaterialActionBody, fixed_action, geometry, sha
from environment_evolution.regenerative_actor.model import ForwardPermit
from environment_evolution.regenerative_actor.runner import control
from local_learning.native_investment_action.intent import MaterialIntent

SRC = Path(__file__).resolve().parents[2]
TASK = 'native_investment_action_20261018'
OUT = SRC/'environment_evolution/results'/TASK
OLD = SRC/'environment_evolution/results/material_action_response_20261018'
DECISION = SRC.parent/'knowledge_base/research/experience_learning'/f'{TASK}.json'
PLAN = OUT/'plan.json'
STOP = False
ERRORS = ('material_error','energy_error','reconstruction_error','K_column_error','transport_error')
# These are actual returned arrays, not retrospective reconstructed receipts.
EVERY = ('K_weights','keep','old_body','transported_body','survivor','born','death','uptake','energy','actor_action_requested')
FEES = ('actor_movement_requested','actor_movement_paid','actor_movement_unpaid',
        'actor_basal_requested','actor_basal_paid','actor_basal_unpaid',
        'synthesis_heat','template_heat','birth_fuel','newborn_energy','precursor_spent',
        'actor_mass_starvation','actor_mass_injury','rule_death','rule_death_energy')

def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');t.replace(p)

def csvwrite(p,rows):
    if not rows:return
    with Path(p).open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def npzfast(path,fields):
    # Lossless float64, low compression level to keep I/O inside the worker cap.
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for k,v in fields.items():
            with z.open(k+'.npy','w',force_zip64=True) as f:
                np.lib.format.write_array(f,np.asarray(v),allow_pickle=False)

def intent_save(path,obj):
    npzfast(path,obj.checkpoint())

def prepare():
    if PLAN.exists():raise RuntimeError('plan already frozen')
    old=json.loads((OLD/'plan_v2.json').read_text())
    refmanifest={r['path']:r for r in json.loads((OLD/'raw_manifest.json').read_text())}
    oldresult=json.loads((OLD/'result.json').read_text())
    assert oldresult['status']=='completed'
    root=Path(oldresult['raw_directory'])
    for p,d in old['source_sha256'].items():
        if sha(p)!=d:raise RuntimeError('old source mismatch; shrink matrix, no replay: '+p)
    refs=[];runs=[]
    for source in old['sources']:
        for variant in ('development','heldout_rotated'):
            for mode,frame,active,horizon in [('none','euler',0,256)]+[(m,'euler',a,a+128) for m in ('gradient','shear_x') for a in (8,128)]+[('shear_x','material',a,a+128) for a in (8,128)]:
                item=dict(source=source['name'],geometry=variant,mode=mode,frame=frame,active_steps=active,horizon=horizon,
                          id=f"{source['name']}--{variant}--{mode}--{frame}--{active}")
                if variant=='development' and frame=='euler' and active in (0,128):
                    folder=root/(source['name']+'--'+mode)
                    files=[folder/f for f in ('initial128.npz','withdraw256.npz','final384.npz','steps.csv','outcome.json')]
                    for p in files:
                        if str(p) not in refmanifest or sha(p)!=refmanifest[str(p)]['sha256']:
                            raise RuntimeError('reference mismatch; no replay: '+str(p))
                    saved=MaterialActionBody.restore(folder/'initial128.npz')
                    expected=MaterialActionBody.from_formation(source['path'],source['sha256'],world=geometry(96,variant))
                    assert saved.clock==expected.clock==128 and saved.elapsed==expected.elapsed==0
                    assert saved.supply_steps==expected.supply_steps==256
                    assert saved.model.config==expected.model.config
                    assert np.array_equal(saved.model.kernels,expected.model.kernels)
                    assert all(np.array_equal(saved.state[k],expected.state[k]) for k in saved.state)
                    assert all(np.array_equal(saved.world[k],expected.world[k]) for k in saved.world)
                    rows=list(csv.DictReader(open(folder/'steps.csv')))
                    assert len(rows)==256 and [int(r['step']) for r in rows]==list(range(129,385))
                    item.update(raw_directory=str(folder),provenance='exact_old_reference',files=[refmanifest[str(p)] for p in files])
                    refs.append(item)
                else:runs.append(item)
    assert len(refs)==6 and sum(r['horizon'] for r in runs)==4192
    paths=[Path(__file__),SRC/'local_learning/native_investment_action/intent.py',DECISION,OLD/'plan_v2.json',OLD/'handoff.json',OLD/'raw_manifest.json']
    paths += [Path(p) for p in old['source_sha256']]
    plan=dict(task=TASK,sources=old['sources'],references=refs,runs=runs,
              source_sha256={str(p):sha(p) for p in paths},planned_new_steps=4192,old_reused_steps=1536,
              host_cap=4608,worker_cap=430,script_wall_limit=200,cpu_threads=2,gpu=0,
              raw_cap_bytes=4*1024**3,d3_exclusive_cap_steps=1536,d3_exclusive_worker_seconds=170,
              request_key='ce2-native-investment-action-20261018-v1',
              physical_rule='unchanged 15691ada host; same two 128 parents, geometry, input horizon256 and prices',
              material_action='initial frozen shear_x times initial rho; real K/keep; divide by final total rho; newborn zero, no normalization',
              registration='all initial mass tracked in every NEW arm; intent is passive bookkeeping for Euler/none arms',
              timing='offset t is postclock128+t; 8 or128 action requests, then128 exactly zero requests',
              comparison='same offset/input to none prefix; not equal realized amplitude or costs',
              positivity='delta whole E > 1e-9: first positive and first point with every subsequent observed delta positive; none if censored through horizon',
              stage_summaries='investment and withdrawal increments separately; U and bodytime cumulative from actual poststep sums; no optimized windows',
              old_missing='per-step synthesis/template/starvation/rule-death and complete K/intent/cohort registration absent: null, not reconstructed',
              retention='every new step saves K/keep/old/transported/survivor/born/death/uptake/E/request plus post-intent and initial-cohort; full maps at 1,8,active end,withdraw end',
              local_feedback='uptake and energy are current local results; cohort-allocated contact is descriptive. Global death ledger is evaluator-only; no paid writer mechanism added',
              completeness='no conditional survivor-only averages; completed death terminal, missing receipts unknown; no learned policy in ce2 stage')
    write(PLAN,plan);print(json.dumps(dict(plan=str(PLAN),sha256=sha(PLAN),new_steps=4192,reused=1536)))

def main():
    global STOP
    start=time.monotonic();plan=json.loads(PLAN.read_text())
    for p,d in plan['source_sha256'].items():
        if sha(p)!=d:raise RuntimeError('frozen source changed: '+p)
    control()
    run=Path(os.environ['LENIA_RUN_DIR']);job=os.environ['LENIA_JOB_ID']
    raw=Path('/data/dataset/lenia-research/lineage_75e3399f4877')/TASK/job
    raw.mkdir(parents=True,exist_ok=False)
    permit=ForwardPermit(plan['host_cap'],str(DECISION));committed=0;outcomes=[];stored=0
    def halt(*_):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,halt);signal.signal(signal.SIGINT,halt)
    def gate():
        if STOP or time.monotonic()-start>plan['script_wall_limit']:raise InterruptedError('time/stop gate')
        if stored>plan['raw_cap_bytes']-32*1024**2:raise InterruptedError('storage reserve reached')
    sources={s['name']:s for s in plan['sources']}
    try:
        for item in plan['runs']:
            control();gate();folder=raw/item['id'];folder.mkdir()
            source=sources[item['source']]
            env=MaterialActionBody.from_formation(source['path'],source['sha256'],world=geometry(96,item['geometry']))
            intent=MaterialIntent(env.state['body'],fixed_action(env.observe_local(),'shear_x'),clock=env.clock)
            env.save(folder/'initial128.npz');intent_save(folder/'intent_initial128.npz',intent)
            initial={k:float(env.state[k].sum()) for k in ('body','energy','fuel','precursor','heat','waste','dead_energy')}
            rows=[];maxerr={};checks={};ledger_error=0.;intent_error=0.
            try:
                for offset in range(1,item['horizon']+1):
                    gate();active=offset<=item['active_steps']
                    obs=env.observe_local()
                    a=intent.readout(obs['body']) if active and item['frame']=='material' else fixed_action(obs,item['mode'] if active else 'none')
                    write(run/'pending.json',dict(path=item['id'],offset=offset,next_attempt=permit.attempted+1))
                    maps,checks=env.step(a,permit=permit);committed+=1
                    tag=intent.advance(maps,final_body=env.state['body'],clock=env.clock)
                    (run/'pending.json').unlink()
                    for k in ERRORS:maxerr[k]=max(maxerr.get(k,0.),abs(checks[k]))
                    ledger_error=max(ledger_error,abs(tag['registration_balance_error']))
                    intent_error=max(intent_error,float(np.max(np.abs(tag['signed_intent_balance_error']))))
                    row=dict(step=env.clock,offset=offset,active=int(active),
                        M=float(env.state['body'].sum()),E=float(env.state['energy'].sum()),
                        U=float(maps['uptake'].sum()),born=float(maps['born'].sum()),death=float(maps['death'].sum()),
                        moved=float(maps['actor_flux_executed'].sum()),
                        requested_work=float(maps['actor_movement_intended_source'].sum()),
                        denied_work=float(maps['actor_movement_denied_source'].sum()),
                        movement_paid=float(maps['actor_movement_paid'].sum()),basal_paid=float(maps['actor_basal_paid'].sum()),
                        basal_unpaid=float(maps['actor_basal_unpaid'].sum()),
                        heat=float(env.state['heat'].sum()),waste=float(env.state['waste'].sum()),dead_energy=float(env.state['dead_energy'].sum()),
                        fuel=float(env.state['fuel'].sum()),precursor=float(env.state['precursor'].sum()),
                        input_E=env.input_energy,input_M=env.input_material,founder=float(env.state['founder_mass'].sum()),
                        action_mass_norm=float((maps['old_body']*np.hypot(*a)).sum()),
                        registered_initial_mass=intent.initial_mass,registered_survivor=float(intent.cohort.sum()),
                        registered_death=float(tag['cohort_death'].sum()),cumulative_registered_death=intent.dead_mass,
                        registered_U=float(tag['cohort_uptake_before_death'].sum()),unknown_contact_mass=tag['unknown_contact_mass'],
                        **{k:float(maps[k].sum()) for k in FEES})
                    rows.append(row)
                    receipt=folder/f'process_{env.clock:06d}.npz'
                    npzfast(receipt,{**{k:maps[k] for k in EVERY},'intent_after':intent.extensive,
                                     'cohort_after':intent.cohort,'cohort_death':tag['cohort_death'],
                                     'postclock':env.clock,'offset':offset})
                    stored+=receipt.stat().st_size
                    if offset in {1,8,item['active_steps'],item['horizon']}:
                        p=folder/f'full_receipt_{env.clock:06d}.npz';npzfast(p,maps);stored+=p.stat().st_size
                    if offset in {item['active_steps'],136,item['horizon']}:
                        p=folder/f'state_{env.clock:06d}.npz';env.save(p);stored+=p.stat().st_size
                        p=folder/f'intent_{env.clock:06d}.npz';intent_save(p,intent);stored+=p.stat().st_size
                    if max(maxerr.values())>1e-8 or ledger_error>1e-7 or intent_error>1e-7:
                        raise RuntimeError('actual conservation/registration boundary failure; evidence retained')
                assert len(rows)==item['horizon']
            finally:
                csvwrite(folder/'steps.csv',rows)
                if rows:
                    env.save(folder/'last_state.npz');intent_save(folder/'last_intent.npz',intent)
                out=dict(**item,provenance='new_native',raw_directory=str(folder),steps=len(rows),initial=initial,
                    final=rows[-1] if rows else None,completed=len(rows)==item['horizon'],max_errors=maxerr,
                    registration_error=ledger_error,intent_balance_error=intent_error,
                    full_initial_registered=True,unknown_physical_calls=permit.attempted-committed)
                outcomes.append(out);write(folder/'outcome.json',out)
                stored=sum(p.stat().st_size for p in raw.rglob('*') if p.is_file())
                write(run/'progress.json',dict(paths=len(outcomes),committed=committed,attempted=permit.attempted,raw_bytes=stored,seconds=time.monotonic()-start))
        assert permit.attempted==plan['planned_new_steps']
    finally:
        write(OUT/'outcomes.json',outcomes)
        result=dict(task=TASK,status='completed' if len(outcomes)==len(plan['runs']) and all(r['completed'] for r in outcomes) else 'incomplete',
            job_id=job,attempted_host_steps=permit.attempted,committed_host_steps=committed,old_reused_steps=1536,
            script_seconds=time.monotonic()-start,raw_directory=str(raw),raw_bytes=stored,
            outcomes=str(OUT/'outcomes.json'),plan_sha256=sha(PLAN),raw_cap_ok=stored<=plan['raw_cap_bytes'])
        write(run/'result.json',result);write(OUT/'result.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':
    import sys
    prepare() if '--prepare' in sys.argv else main()
