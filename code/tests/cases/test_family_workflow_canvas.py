"""Exercise the canvas projection functions with nested workflow ownership.

:design: decision_44cf587c1721, decision_5b5653c3eaa5
"""
import json
from pathlib import Path
import subprocess


def test_nested_family_workflow_visibility_and_focus():
    """嵌套Family折叠展开与邻域保留工作流可见性

    Family工作流元素的受控归属、生命周期、原业务身份与画布折叠。
    """
    source = (Path(__file__).parents[2] / "management_portal/static/hatg_panel.js").read_text()
    names = ("functionBlockElements", "familyRoles", "familyInstances", "familyForFacade",
             "familyExpanded", "foldedFamilyObjects", "familyObjectIds", "familyMembership")
    functions = []
    for name in names:
        start = source.index("  function " + name + "(")
        end = source.index("\n  }", start) + len("\n  }")
        functions.append(source[start:end])
    script = "\n".join(functions) + r'''
const assert = require('node:assert/strict');
const state = {lineage: {function_blocks: []}, expandedFamilyFacadeIds: new Set()};
const functionBlockInstanceById = id => state.lineage.function_blocks.find(b => b.id === id);
const familyTone = () => 1;
const element = (id, kind, key) => ({id, kind, key});
const child = {id:'child', title:'Child', family:{facade:'child-face', steward:'cs', router:'cr', members:['worker']},
  elements:[element('worker','resident','member:worker'), element('event','component','workflow:event'),
            element('effect','component','workflow:effect'), element('router-effect','component','router')]};
const root = {id:'root', title:'Root', family:{facade:'root-face', steward:'rs', router:'rr', members:['child-face']},
  elements:[element('child','function_block','member:child-face'),
            element('shared','component','workflow:shared')]};
for (const blocks of [[root, child], [child, root]]) {
  state.lineage.function_blocks = blocks;
  for (const rootOpen of [false, true]) for (const childOpen of [false, true]) {
    state.expandedFamilyFacadeIds = new Set([
      ...(rootOpen ? ['root-face'] : []), ...(childOpen ? ['child-face'] : [])]);
    const hidden = foldedFamilyObjects();
    assert.equal(hidden.residents.has('event'), !(rootOpen && childOpen));
    assert.equal(hidden.residents.has('effect'), !(rootOpen && childOpen));
    assert.equal(hidden.residents.has('shared'), !rootOpen);
    assert.equal(hidden.residents.has('router-effect'), true);
    if (rootOpen && childOpen) {
      const focused = familyObjectIds(familyForFacade('root-face'));
      for (const id of ['event','effect','shared','worker']) assert(focused.has(id));
      assert(!focused.has('router-effect'));
      assert.equal(familyMembership(hidden.residents).get('event').facade, 'child-face');
    }
  }
}
console.log(JSON.stringify({nested_visibility:true, focus:true, ownership_tone:true}));
'''
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert all(json.loads(result.stdout).values())
