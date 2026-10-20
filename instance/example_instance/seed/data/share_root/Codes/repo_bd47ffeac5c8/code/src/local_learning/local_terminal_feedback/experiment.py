"""One serial bounded native use test; no replay of ce2's incomplete matrix."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import numpy as np
from .core import RecentRecords, divide
from local_learning.native_investment_action.intent import MaterialIntent
from local_learning.method_renewal_local_plasticity.anticipatory_io import dump, sha
from environment_evolution.native_investment_action.run import npzfast

SRC = Path(__file__).resolve().parents[2]
OUT = SRC/'local_learning/results_20261018/local_terminal_feedback'
KEY = 'd3-local-terminal-feedback-20261018-v1'
STOP = False


def control():
    values = {}
    for key in ('research.desired_state', 'research.started'):
        proc = subprocess.run(['python3', '${code_root}/scripts/resident_hatg_instruction.py',
            '--resident-id', 'resident_d3ccfa2d95b7', 'call', 'GetSharedState', '--arguments-json',
            json.dumps({'lineage_id':'lineage_75e3399f4877','key':key})],
            capture_output=True, text=True, check=True)
        value = json.loads(proc.stdout)
        value = value.get('result', value)
        values[key] = value.get('value', value.get('state',{}).get(key))
    if values != {'research.desired_state':'running','research.started':True}:
        raise RuntimeError('control does not permit physics '+str(values))
    return values


def pack(path, data):
    arrays = {}
    def rec(v, key):
        if isinstance(v, np.ndarray):
            arrays[key] = v
            return {'array': key}
        if isinstance(v, dict):
            return {k: rec(x, key+'__'+k) for k, x in v.items()}
        if isinstance(v, (tuple, list)):
            return [rec(x, key+'__'+str(i)) for i, x in enumerate(v)]
        return v.item() if isinstance(v, np.generic) else v
    arrays['metadata'] = np.asarray(json.dumps(rec(data, 'record'), allow_nan=False))
    npzfast(path, arrays)
    return {'path': str(path), 'sha256': sha(path)}


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT/'plan.json').exists():
        raise FileExistsError('already frozen')
    hp = SRC/'environment_evolution/results/local_terminal_feedback_20261018/handoff.json'
    assert sha(hp) == 'cc1b952d78e5bf0a7177da633f53e6a9908de9e1445f7f5647c096c13cc9107d'
    host = json.loads(hp.read_text())
    old = json.loads((SRC/'local_learning/results_20261018/material_action_response/plan.json').read_text())
    files = [Path(__file__), Path(__file__).with_name('core.py'), hp,
        SRC/'environment_evolution/local_terminal_feedback/core.py',
        SRC/'environment_evolution/native_investment_action/run.py',
        SRC/'local_learning/native_investment_action/intent.py',
        SRC/'local_learning/method_renewal_local_plasticity/anticipatory_io.py']
    files += [Path(p) for p in old['sources'] if not ('material_action_response/experiment' in p or 'material_action_response/core' in p and 'local_learning' in p)]
    decision = SRC.parent/'knowledge_base/research/experience_learning/local_terminal_feedback_20261018.json'
    files.append(decision)
    for b in old['bodies']:
        assert sha(b['path']) == b['sha256']
    assert sha(SRC/'environment_evolution/local_terminal_feedback/core.py') == host['core_sha256']
    plan = dict(task='local_terminal_feedback_20261018', key=KEY,
        source_sha256={str(p): sha(p) for p in sorted(set(files))}, bodies=old['bodies'],
        host_handoff=str(hp), host_sha256=sha(hp),
        geometry='heldout_rotated', geometry_status='known host layout, NOT a fresh confirmation world',
        seeds=[2026101811501, 2026101811502], arms=['recent','ignore','frozen','euler_unit','euler','none'],
        prefix_units=1, future_units=2, unit_steps=64, active_steps=32,
        planned_native_steps=1664, step_formula='2*(64+6*128)',
        native_cap=4864, worker_cap=420, script_wall_limit=195., cpu_threads=2, gpu=0,
        raw_cap_bytes=4*1024**3-host['raw_bytes'], host_steps_before=771,
        host_worker_seconds_before=174.508036, old_budget_used=0,
        input_horizon=256, actual_source_elapsed_end=192,
        estimation='fixed discount .5; one direct cumulative and newly unread message observation at each completed64-step unit; (contact-motor-basal)/exposure; no global returns',
        carriers='separate direct/message extensive statistics; true K then old keep; newborn no ancestor state',
        action='none, current local gradient, Euler shear, material shear; unit code cohorts give current old-mass fractions; material vector follows K/keep, no amplification; 32investment then32zero requests',
        euler_baselines='euler_unit uses same registered support; euler additionally refreshes full local shear including new material, a stronger fixed control, not an isolated memory contrast',
        prefix='one actual randomized8x8 tile unit; all arms restore identical complete body/records/learner/RNG after its single consume',
        learning='descriptive local received-record exploitation, NOT full initial-cohort or organism causal credit; no fit/search/network',
        ignore='same actual communication; all message content excluded from estimator readout and future updates; inherited direct state preserved',
        frozen='same actual communication; no content update, but carrying/death/birth dilution remain',
        evaluation='two bodies in one known layout; no independent-world statistical claim, no shape-as-replicate; full negative outcomes',
        pricing='native movement/basal/growth/template + actual terminal delivery; compute/storage/writer unpriced')
    dump(OUT/'plan.json', plan)
    print(json.dumps({'plan':str(OUT/'plan.json'),'sha256':sha(OUT/'plan.json'),'steps':1664}))


def main():
    from environment_evolution.local_terminal_feedback.core import TerminalFeedbackBody
    from environment_evolution.material_action_response.core import geometry, fixed_action
    from environment_evolution.regenerative_actor.model import ForwardPermit
    plan = json.loads((OUT/'plan.json').read_text())
    for p, h in plan['source_sha256'].items():
        if sha(p) != h:
            raise RuntimeError('frozen source mismatch '+p)
    control()
    job = os.environ['LENIA_JOB_ID']; run = Path(os.environ['LENIA_RUN_DIR'])
    raw = Path('/data/dataset/lenia-research/lineage_75e3399f4877/local_terminal_feedback_learning_20261018')/job
    raw.mkdir(parents=True, exist_ok=False)
    start = time.monotonic(); permit = ForwardPermit(plan['planned_native_steps'], str(OUT/'plan.json'))
    stages = []; captures = []; matches = []; completed = 0; failure = None; live = {}
    ledger = (raw/'steps.jsonl').open('w')
    def halt(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, halt)
    def gate():
        if STOP or time.monotonic()-start > plan['script_wall_limit']:
            raise InterruptedError('stop/time gate')
    def snapshot(env, learner, label):
        p = raw/label; env.save(p)
        lr = pack(raw/(label+'_learner.npz'), learner.checkpoint())
        return dict(body=str(p), learner=lr)
    def rollout(env, learner, label, arm, units):
        nonlocal completed
        gate(); live.update(env=env,learner=learner,label=label)
        stage = dict(label=label, arm=arm, steps=0, start_clock=env.clock,
            M0=float(env.state['body'].sum()), E0=float(env.state['energy'].sum()),
            U=0., body_time=0., movement=0., basal=0., basal_unpaid=0., communication=0.,
            born=0., death=0., input_E=0., input_M=0., updates=0, max_error=0., units=[])
        stages.append(stage)
        for unit in range(units):
            gate()
            obs = env.observe_local(); before = learner.checkpoint()
            codes, prediction = learner.choose(obs['body'], arm)
            pre = dict(public=obs, learner=before, codes=codes, prediction=prediction, clock=env.clock)
            env.begin_unit(codes)
            shear = fixed_action(obs, 'shear_x')
            intent = MaterialIntent(obs['body'], shear*(codes == 3), clock=env.clock)
            initial_mass = env.registered_initial
            for k in range(plan['unit_steps']):
                gate()
                obs = env.observe_local()
                if k >= plan['active_steps'] or arm == 'none':
                    action = np.zeros((2,*obs['body'].shape))
                elif arm == 'euler':
                    action = fixed_action(obs, 'shear_x')
                else:
                    fractions = divide(env.direct[:,0], obs['body'])
                    action = fixed_action(obs,'gradient')*fractions[1] + fixed_action(obs,'shear_x')*fractions[2] + intent.readout(obs['body'])
                    # Only remove floating excess; never restore decayed amplitude.
                    action /= np.maximum(1., np.hypot(action[0],action[1]))
                maps, receipt, checks = env.step(action, permit=permit); completed += 1
                stage['steps'] += 1
                learner.transport(maps)
                tag = intent.advance(maps, final_body=env.state['body'], clock=env.clock)
                totals = {key:float(maps[key].sum()) for key in (
                    'uptake','born','death','actor_movement_requested','actor_movement_paid','actor_movement_unpaid',
                    'actor_basal_requested','actor_basal_paid','actor_basal_unpaid','synthesis_heat','template_heat',
                    'birth_fuel','newborn_energy','precursor_spent','input_energy','input_material','rule_death','rule_death_energy')}
                row = dict(stage=label, unit=unit, unit_offset=k+1, clock=env.clock,
                    M=float(env.state['body'].sum()), E=float(env.state['energy'].sum()),
                    heat=float(env.state['heat'].sum()), waste=float(env.state['waste'].sum()),
                    dead_energy=float(env.state['dead_energy'].sum()), communication=float(receipt['paid'].sum()),
                    generated=float(receipt['generated'][:,:,0].sum()), delivered=float(receipt['incoming'][:,0].sum()),
                    undelivered=float((receipt['undelivered_no_receiver'][:,:,0]+receipt['undelivered_unpaid'][:,:,0]).sum()),
                    lost_message=float((receipt['message_lost_actor'][:,0]+receipt['message_lost_rule'][:,0]).sum()),
                    **totals, checks=checks)
                ledger.write(json.dumps(row)+'\n')
                for dst, src in [('U','uptake'),('movement','actor_movement_paid'),('basal','actor_basal_paid'),
                                 ('basal_unpaid','actor_basal_unpaid'),('born','born'),('death','death'),
                                 ('input_E','input_energy'),('input_M','input_material')]:
                    stage[dst] += totals[src]
                stage['body_time'] += row['M']*env.body.model.dt
                stage['communication'] += row['communication']
                stage['max_error'] = max(stage['max_error'], *[abs(checks[key]) for key in
                    ('material_error','energy_error','transport_error','reconstruction_error','communication_energy_error','terminal_balance_error')])
                if k in (0,31,63):
                    captures.append(pack(raw/f'{label}_u{unit}_step{k+1:02d}.npz',
                        dict(clock=env.clock,action=action,body=env.state['body'],energy=env.state['energy'],
                             intent=intent.checkpoint(),maps=maps,receipt=receipt)))
            feedback = env.consume_feedback(decision_clock=env.clock+1)
            updated = learner.update(feedback, maps['survivor'], arm)
            stage['updates'] += int(updated)
            # Reading again is a pure atomicity assertion, no physical call.
            assert env.consume_feedback(decision_clock=env.clock+1) is None
            post = dict(clock=env.clock, learner=learner.checkpoint(), feedback=feedback,
                        unit_initial_mass=initial_mass, unit_terminal_mass=env.terminal_mass,
                        unit_live_mass=float(env.direct[:,0].sum()), body=env.state['body'],energy=env.state['energy'])
            rec = pack(raw/f'{label}_unit{unit}_decision.npz',dict(pre=pre,post=post))
            stage['units'].append(rec)
            stage['last_complete']=snapshot(env,learner,f'{label}_unit{unit}_complete')
            if sum(p.stat().st_size for p in raw.rglob('*') if p.is_file()) > plan['raw_cap_bytes']:
                raise RuntimeError('raw byte budget')
            ledger.flush();dump(raw/'stages.json',stages)
        stage.update(M=float(env.state['body'].sum()),E=float(env.state['energy'].sum()),end_clock=env.clock,
            alive=bool(np.any(env.state['body']>0)),heat=float(env.state['heat'].sum()),
            waste=float(env.state['waste'].sum()),dead_energy=float(env.state['dead_energy'].sum()))
        return stage
    try:
        for bi, body in enumerate(plan['bodies']):
            env=TerminalFeedbackBody.from_formation(body['path'],body['sha256'],world=geometry(96,plan['geometry']))
            learner=RecentRecords((96,96),plan['seeds'][bi])
            initial=snapshot(env,learner,f'b{bi}_initial')
            prefix=rollout(env,learner,f'b{bi}_prefix','prefix',plan['prefix_units'])
            common=learner.checkpoint(); parent=prefix['last_complete']['body']
            for arm in plan['arms']:
                child=TerminalFeedbackBody.restore(parent); copy=RecentRecords.restore(common)
                equal=all(np.array_equal(child.state[k],env.state[k]) for k in env.state)
                equal=equal and all(np.array_equal(getattr(child,k),getattr(env,k)) for k in ('direct','message','unread','message_support'))
                assert equal and child.clock==env.clock and child.body.elapsed==env.body.elapsed
                matches.append(dict(body=bi,arm=arm,exact_physical_and_records=True,parent=parent,
                                    exact_learner=True,clock=child.clock,common_rng=common['rng']))
                rollout(child,copy,f'b{bi}_{arm}',arm,plan['future_units'])
        assert completed == plan['planned_native_steps']
    except BaseException as exc:
        failure=dict(type=type(exc).__name__,message=str(exc),label=live.get('label'),committed=completed,attempted=permit.attempted)
        if live:
            failure['last_complete']=snapshot(live['env'],live['learner'],'interrupted_complete')
    finally:
        ledger.close()
        dump(raw/'stages.json',stages);dump(raw/'captures.json',captures);dump(raw/'common_start_checks.json',matches)
        result=dict(status='completed' if failure is None else 'incomplete',job=job,attempted=permit.attempted,
            committed=completed,failed_attempts=permit.attempted-completed,failure=failure,raw_directory=str(raw),
            wall_seconds=time.monotonic()-start,plan_sha256=sha(OUT/'plan.json'),stages=str(raw/'stages.json'),
            objective_met=False,scientific_result=True)
        dump(run/'result.json',result);dump(OUT/'result.json',result)
        print(json.dumps(result))
    if failure:
        raise RuntimeError(str(failure))


if __name__ == '__main__':
    import sys
    prepare() if '--prepare' in sys.argv else main()
