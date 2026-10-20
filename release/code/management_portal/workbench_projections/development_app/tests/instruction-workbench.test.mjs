import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {readContent, visibleInstructions, restoreState, experiment, declarationExplanation,
  lessonFor, representativeFor, purposeSummary, sourceForLesson, declarationOwners} from '../src/architecture/instruction-workbench.model.js';

// Authored local teaching content can be read independently of code revisions.
const snapshot = JSON.parse(readFileSync(new URL('./fixtures/instruction-catalog.json', import.meta.url), 'utf8'));
const catalog = snapshot.catalog;
const fixture = () => structuredClone(snapshot);
const data = readContent(fixture());

test('catalog needs no commit or timestamp to be read', () => {
  const content = fixture(); delete content.provenance.commit; delete content.provenance.observed_at;
  assert(readContent(content));
  assert.equal(sourceForLesson('ReadResource').includes('/commit/'), false);
});

test('the published ISA has renamed instructions and no retired entry points', () => {
  for (const name of ['Functionalize','Unfunctionalize','GetKnowledgeTags','UpdateKnowledgeTags','UpdateProviderAccount'])
    assert.ok(catalog.schemas[name], name);
  for (const name of ['Functionlize','Unfunctionlize','GetProjectTags','UpdateProjectTags','UpdateSchedulerSettings',
    'ListFunctionBlocks','GetResidentState','CancelMailboxItem','ListMailbox','UpdateUserCategory','EmitEvent','GetTriggerPathStep','GetFunctionBlock']) {
    assert.equal(catalog.schemas[name], undefined, name);
    assert.equal(lessonFor(name), null, name);
  }
  assert.equal(data.categories.find(c=>c.id==='resident_execution').title,'Resident 执行');
});

test('authored experiments remain readable when documentation is older than code', () => {
  const older = readContent({...fixture(), provenance: {...data.provenance, commit: 'different'}});
  assert(lessonFor('UpdateSharedState', older.provenance));
  assert(representativeFor(older, catalog.schemas.UpdateSharedState.category));
});

test('catalog reading keeps purpose short and ranks instruction names before body mentions', () => {
  const description = catalog.schemas.DeliverMessage.description;
  assert.ok(purposeSummary(description).length < description.length);
  assert.equal(purposeSummary(description), description.split('。')[0]);
  assert.equal([...purposeSummary('甲'.repeat(100))].length, 72);
  assert.equal(purposeSummary('读取正文。第二句话'), '读取正文');
  assert.equal(purposeSummary('首行\n后文'), '首行');
  const names = visibleInstructions(data, {query: '  deliverMESSAGE  '}).map(i => i.instruction);
  assert.equal(names[0], 'DeliverMessage');
  assert.ok(names.includes('PromoteSeed'));
  assert.ok(names.indexOf('PromoteSeed') > names.indexOf('DeliverMessage'));
  assert.deepEqual(visibleInstructions(data, {}), data.catalog.instructions);
  assert.ok(visibleInstructions(data, {query:'DeliverMessage', category:'resident'}).every(i => i.category === 'resident'));
});

test('all formal categories and their authored examples are readable', async () => {
  assert.deepEqual(new Set(data.categories.map(c => c.id)), new Set(catalog.instructions.map(c => c.category)));
  assert.equal(data.catalog.instructions.length, Object.keys(catalog.schemas).length);
  for (const category of data.categories) {
    const representative = representativeFor(data, category.id);
    assert.ok(representative, category.id);
    assert(lessonFor(representative));
    const state = restoreState(data, {category: category.id});
    assert.equal(visibleInstructions(data, state).length, category.count);
  }
});
test('malformed directory definitions still fail explicitly', async () => {
  const mixed = fixture(); mixed.catalog.schemas.ReadResource.owner = 'web';
  assert.throws(() => readContent(mixed), /同一批次/);
  assert.throws(() => readContent({...fixture(), catalog: {instructions: [], schemas: {}}}), /为空/);
});
test('argument-based routing is part of a lesson declaration, not just display metadata', async () => {
  const schema = catalog.schemas.GetMailbox;
  const changed = structuredClone(schema);
  changed.argument_owners[0].owner = 'resource';
  const route = declarationExplanation(schema).route;
  assert.match(route, /resident_id.*SocietyService/);
  assert.match(route, /其他合法参数组合.*Portal Web/);
  assert.deepEqual(declarationOwners(schema), [
    {owner:'lineage', title:'SocietyService', condition:'带 resident_id'},
    {owner:'web', title:'Portal Web', condition:'其他合法参数组合'},
  ]);
});
test('CAS distinguishes value mismatch, unchanged match and actual replacement', () => {
  assert.deepEqual(experiment('UpdateSharedState', 'changed', true).result, {matched:false,changed:false,value:5});
  assert.deepEqual(experiment('UpdateSharedState', 'same', true).result, {matched:true,changed:false,value:3});
  assert.deepEqual(experiment('UpdateSharedState', 'match', true).result, {matched:true,changed:true,value:4});
  assert.equal(experiment('UpdateSharedState', 'match', false).result, null);
});
test('receipts preserve the distinction between delivery, running, observation and completion', () => {
  assert.equal(experiment('DeliverMessage', 'busy', true).result.input_state.state, 'queued');
  assert.deepEqual(experiment('DeliverMessage', 'human', true).result.input_state, {});
  assert.ok(experiment('CloseIntake', 'working', true).after.some(t => t.includes('A：继续')));
  assert.equal(experiment('RunScript', 'start', true).result.status, 'running');
  assert.ok(experiment('ReadResource', 'large', true).result.download_url);
  assert.equal(experiment('ReadResource', 'large', true).result.text, undefined);
  assert.equal(experiment('WatchConversationEvents', 'empty', true).result.high_watermark, 16);
  assert.deepEqual(experiment('GetGpuStatus', 'unknown', true).result.devices, []);
});
test('updated user, mailbox and cancellation lessons explain their actual state boundaries', () => {
  assert.equal(experiment('UpdateUser','category',true).result.category_changed,true);
  assert.equal(experiment('UpdateUser','category_same',true).result.category_changed,false);
  const user = experiment('GetMailbox','user',true).result;
  const resident = experiment('GetMailbox','resident',true).result;
  assert.equal(user.unread_count,1); assert.equal(user.items[0].unread,true);
  assert.equal('pending' in user,false); assert.equal('items' in resident,false);
  assert.equal(resident.pending[0].kind,'request');
  assert.equal(experiment('GetMailbox','both',true).result.error,'instruction.invalid_arguments');
  assert.equal(experiment('GetMailbox','resident_filter',true).result.error,'instruction.invalid_arguments');
  const running=experiment('CancelInput','running',true).result;
  assert.equal(running.cancellation_requested,true); assert.equal(running.backend_cancelled,false);
  assert.equal(running.input_state.state,'running');
  for (const state of ['completed','failed','cancelled']) {
    const receipt=experiment('CancelInput',state,true).result;
    assert.equal(receipt.already_terminal,true); assert.equal(receipt.input_state.state,state);
  }
});
test('manual triggers, Pulse publication and Family relay retain their different meanings', () => {
  assert.equal(experiment('TriggerEvent','system',true).result.error,'instruction.invalid_arguments');
  assert.equal(experiment('TriggerEvent','both',true).result.error,'instruction.invalid_arguments');
  assert.ok(experiment('TriggerEvent','named',true).after.some(v=>v.includes('manual.triggered')));
  const first=experiment('PublishPulseItem','own',true).result.publication;
  assert.equal(experiment('PublishPulseItem','repeat',true).result.publication.publication_id,first.publication_id);
  assert.equal(experiment('PublishPulseItem','foreign',true).result.error,'instruction.forbidden');
  assert.equal(experiment('RelayFamilyMessage','quota',true).result.status,'waiting_for_quota_review');
  assert.equal(experiment('RelayFamilyMessage','reviewed',true).result.status,'relayed');
  assert.equal(experiment('RelayFamilyMessage','wrong_reviewer',true).result.error,'instruction.forbidden');
});
test('owner explanation respects execution-record routing and read-only self description', () => {
  assert.match(declarationExplanation(catalog.schemas.GetInstructionExecution).route, /原服务/);
  assert.match(declarationExplanation(catalog.schemas.ListInstructions).route, /入口直接/);
  assert.match(declarationExplanation(catalog.schemas.WatchConversationEvents).effect, /不等于取消/);
});
test('restore filters removed instructions and invalid categories while preserving local reading state', () => {
  const state = restoreState(data, {category:'lineage_state', query:'UpdateSharedState', instruction:'UpdateSharedState',variant:'changed',step:1});
  assert.equal(state.variant, 'changed'); assert.equal(state.step, 1);
  const cleaned = restoreState(data, {...state, category:'removed',instruction:'NotAnInstruction'});
  assert.equal(cleaned.category, ''); assert.notEqual(cleaned.instruction, 'NotAnInstruction');
  assert.ok(lessonFor(cleaned.instruction));
  assert.equal(visibleInstructions(data, {query:'after_cursor'}).some(i => i.instruction==='WatchConversationEvents'),true);
});

test('example success receipt keys come from the real result declaration', () => {
  for (const item of catalog.instructions) {
    const lesson = lessonFor(item.instruction); if (!lesson) continue;
    for (const variant of lesson.variants) {
      if (variant.result.error) continue;
      const declared = new Set(catalog.schemas[item.instruction].result.map(p => p.name));
      for (const key of Object.keys(variant.result)) assert.ok(declared.has(key), item.instruction + ': ' + key);
    }
  }
});
