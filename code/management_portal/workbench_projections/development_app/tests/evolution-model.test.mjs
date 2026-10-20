import test from 'node:test';
import assert from 'node:assert/strict';
import { initialScene, reduceScene, restoreScenes } from '../src/evolution/model.js';
const direction={id:'goal-grown-studio',visual:{storyboard:['seed','team','product','grow'].map(id=>({id}))}};
test('local reservation requires slot and purpose and retains submitted result independently of later input',()=>{
 const initial=initialScene(direction);assert.equal(reduceScene(direction,initial,{type:'reserve'}).studio.reservation,null);
 let s=reduceScene(direction,initial,{type:'studio',value:{slot:'09:00',purpose:' microscopy '}});
 s=reduceScene(direction,s,{type:'reserve'});s=reduceScene(direction,s,{type:'studio',value:{purpose:'new input',maintenance:true}});
 assert.deepEqual(s.studio.reservation,{slot:'09:00',purpose:'microscopy'});assert.equal(s.studio.maintenance,true);assert.equal(initial.studio.reservation,null);
 assert.equal(reduceScene(direction,s,{type:'reset'}).studio.reservation,null);
});
test('new selection invalidates local grouping; new source cannot restore old scene',()=>{
 let s=initialScene(direction);s=reduceScene(direction,s,{type:'member',id:0});s=reduceScene(direction,s,{type:'group'});
 assert.deepEqual(s.command.members,[0]);assert.equal(s.command.grouped,true);
 s=reduceScene(direction,s,{type:'member',id:0});assert.equal(s.command.grouped,false);
 s=reduceScene(direction,s,{type:'step',value:99});assert.equal(s.step,3);
 const facts={directions:[direction],sources:{evolution:{version:'new'}}};
 assert.equal(restoreScenes(facts,{schema:'evolution-gallery.v1',baseline:'old',scenes:{[direction.id]:s}})[direction.id].step,0);
 assert.equal(restoreScenes(facts,{schema:'evolution-gallery.v1',baseline:JSON.stringify(facts.sources),scenes:{[direction.id]:s}})[direction.id].step,3);
});
