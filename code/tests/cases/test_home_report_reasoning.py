"""Source semantics and computational explanation; no real resource mutation."""
import copy
import unittest
from management_portal.home_report_reasoning import build_reasoning


def report():
    return {'target':{'kind':'project','id':'fixture','focus':'a'},'warnings':[],
            'rows':[{'id':key,'title':key,'sources':['map'],'status':'执行失败'} for key in 'abcde'],
            'relations':[{'from':a,'to':b,'kind':kind} for a,b,kind in [
                ('b','a','depends_on'),('c','b','depends_on'),('d','a','depends_on'),('e','a','builds_on')]]}


class ReasoningTests(unittest.TestCase):
    def test_reverse_chain_branch_and_no_state_inference(self):
        source=report(); before=copy.deepcopy(source);plan=build_reasoning(source)
        self.assertEqual(source,before)
        self.assertEqual(plan['mode'],'dependency')
        self.assertEqual(plan['before']['reached'],[])
        self.assertEqual(plan['after']['reached'],['b','c','d'])
        self.assertEqual([(p['from'],p['to']) for p in plan['after']['trace']],[('a','b'),('a','d'),('b','c')])
        self.assertEqual(plan['unconnected'],['e'])
        self.assertIn('不表示实际工作已阻塞',plan['assumption'])
        self.assertEqual([b['edge'] for b in plan['steps'][3]['beats'] if 'edge' in b],['e0','e2','e1'])
        source['target']['focus']='b'
        self.assertEqual(build_reasoning(source)['after']['reached'],['c'])

    def test_cycles_and_mixed_relations(self):
        source=report()
        source['relations'] += [{'from':'a','to':'c','kind':'depends_on'},
                                {'from':'e','to':'c','kind':'alternative'},
                                {'from':'e','to':'b','kind':'converges'}]
        plan=build_reasoning(source)
        self.assertEqual(plan['after']['reached'],['b','c','d'])
        self.assertEqual(len(plan['after']['trace']),3)
        self.assertEqual(plan['unconnected'],['e'])

    def test_empty_dependency_is_unknown_not_no_impact(self):
        source=report()
        for edge in source['relations']:edge['kind']='builds_on'
        plan=build_reasoning(source)
        self.assertEqual(plan['evidence_status'],'insufficient')
        self.assertIn('未知',plan['steps'][-1]['speech'])
        self.assertIn('不能把空路径',plan['steps'][-1]['speech'])

    def test_partial_invalid_or_unbounded_input_declines(self):
        for mutate in [
            lambda r:r['warnings'].append('不可读/截断'),
            lambda r:r['relations'].append({'from':'private','to':'a','kind':'depends_on'}),
            lambda r:r['rows'].extend([{'id':str(i),'title':'x','sources':['map']} for i in range(10)]),
            lambda r:r['rows'][0].update(sources=[]),
            lambda r:r.update(relations=[]),
            lambda r:r['rows'].append(r['rows'][0]),
        ]:
            source=report();mutate(source)
            self.assertFalse(build_reasoning(source)['available'])

    def test_dense_and_long_graphs_stay_textual(self):
        source=report()
        source['relations']=[{'from':k,'to':'a','kind':'depends_on'} for k in 'bcde']
        self.assertFalse(build_reasoning(source)['available'])
        source['relations']=[{'from':a,'to':b,'kind':'depends_on'} for a,b in zip('bcde','abcd')]
        self.assertFalse(build_reasoning(source)['available'])

if __name__=='__main__':unittest.main()
