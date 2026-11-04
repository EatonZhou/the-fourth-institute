"""Pure source-mapped reasoning; no execution-state inference or new facts."""
from collections import deque

LABELS = {'builds_on': '建立在…之上', 'depends_on': '依赖', 'converges': '汇合', 'alternative': '替代'}


def walk(seed, edges, blocked=()):
    """Reverse depends_on only: source from depends on source to. Cycles stop."""
    seen, queue, trace = {seed}, deque([seed]), []
    while queue:
        current = queue.popleft()
        if current in blocked:
            continue
        for edge in edges:
            if edge['kind'] != 'depends_on' or edge['to'] != current:
                continue
            nxt = edge['from']
            if nxt in seen or nxt in blocked:
                continue
            seen.add(nxt)
            queue.append(nxt)
            trace.append({'edge': edge['id'], 'from': current, 'to': nxt})
    return {'reached': sorted(seen - {seed}), 'trace': trace}


def build_reasoning(report):
    rows = report['rows']
    def unavailable(reason):
        return {'available': False, 'reason': reason + '；仍可阅读文字报告。'}
    if report.get('warnings'):
        return unavailable('来源含未读、截断或未核对范围，不能生成完整图的推理')
    if not 2 <= len(rows) <= 10:
        return unavailable('连续图解支持当前报告中 2–10 个可核对对象')
    ids = {r['id'] for r in rows}
    if len(ids) != len(rows):
        return unavailable('对象标识重复')
    raw = report.get('relations', [])
    if not raw:
        return unavailable('当前没有可视化的登记关系，不用正文或状态卡代替结构解释')
    if any(e.get('from') not in ids or e.get('to') not in ids or not e.get('kind') for e in raw):
        return unavailable('关系存在不可读或缺失的端点、类型')
    if any(not r.get('sources') for r in rows):
        return unavailable('对象缺少可核对的来源')
    edges = [{**e, 'id': f'e{i}', 'label': LABELS.get(e['kind'], e['kind'])} for i, e in enumerate(raw)]
    candidates = sorted({e['to'] for e in edges if e['kind'] == 'depends_on'})
    mode = 'dependency' if candidates else 'insufficient'
    results = {key: walk(key, edges) for key in candidates}
    focus = report.get('target', {}).get('focus', '')
    seed = next((key for key in candidates if focus and (key == focus or key.endswith('/' + focus))), None)
    seed = seed or (max(results, key=lambda key: len(results[key]['reached'])) if results else edges[0]['to'])
    after, before = walk(seed, edges), walk(seed, edges, {seed})
    levels = {seed: 0}
    for step in after['trace']:
        levels[step['to']] = levels[step['from']] + 1
    # Put unconnected objects in a separate lower row, never infer independence.
    unconnected = sorted(ids - {seed} - set(after['reached']))
    groups = {}
    for index, row in enumerate(rows):
        # A relation-classification diagram uses a neutral grid, not inferred levels.
        column = levels.get(row['id'], -1) if mode == 'dependency' else index % 3
        groups.setdefault(column, []).append(row)
    if max(levels.values()) > 3 or any(len(group) > 3 for group in groups.values()):
        return unavailable('当前图超过可辨认布局范围（至多四层、每层三个对象），请聚焦较小的正式来源')
    nodes = []
    for level, group in sorted(groups.items()):
        for slot, row in enumerate(group):
            x = (.18 + .64 * slot / max(1, len(group)-1)) if level < 0 else .16 + .68 * level / max(1, max(groups))
            y = .86 if level < 0 else (.36 if len(group)==1 else .18 + .46*slot/(len(group)-1))
            nodes.append({'id': row['id'], 'title': row['title'], 'sources': row['sources'],
                          'x': x, 'y': y, 'depth': levels.get(row['id'])})
    title = next(n['title'] for n in nodes if n['id'] == seed)
    assumption = f'本地假设：暂时禁止遍历「{title}」。不表示实际工作已阻塞；不修改来源。'
    limit = '仅当前可读登记图；不预测任务完成、时间或业务因果；未连入不证明真实独立。循环去重，每个对象只计一次。'
    rule = '原边 from→to 表示 from 依赖 to；追踪方向 to→from。仅 depends_on 参与计算。'
    direct = [n['title'] for n in nodes if n['depth'] == 1]
    indirect = [n['title'] for n in nodes if n['depth'] is not None and n['depth'] > 1]
    steps = [
        {'title':'构造可核对对象','mode':'objects',
         'speech':f'恢复一个假设禁用的起点，登记图中的可达范围怎样变化？先看{len(nodes)}个对象。蓝色起点是{title}。'},
        {'title':'原关系与反向追踪','mode':'relations',
         'speech':f'逐条画出{len(edges)}条原关系。蓝色依赖箭头指向它所依赖的对象。紫色追踪将从起点反向查找依赖者。灰色的其他关系不参与计算。'},
        {'title':'明确假设：禁用起点','mode':'blocked',
         'speech':f'现在假设禁止遍历{title}。橙色门表示本地约束，不代表真实阻塞。遍历停在起点，新增可达集合为空。所有原始关系保持不变。'},
        {'title':'恢复起点，逐跳比较','mode':'compare',
         'speech':f'左边保留假设，右边移除同一道门。先沿反向边到达{"、".join(direct) or "直接依赖者"}。' +
                  (f'再从已到达的对象继续，找到{"、".join(indirect)}。' if indirect else '本图没有更多间接可达对象。') +
                  '紫色轨迹逐跳展开。每个对象只访问一次，循环不会重复计数。'},
        {'title':'集合差异与结论边界','mode':'conclusion',
         'speech':f'两侧只改变起点是否可遍历。右侧比左侧多{len(after["reached"])}个可达对象。' +
                  (f'{"、".join(n["title"] for n in nodes if n["id"] in unconnected)}没有进入本次集合。' if unconnected else '') +
                  '这只说明当前登记依赖图的结构，不能据此判断完成、延误或真实独立。'}
    ]
    if mode == 'insufficient':
        assumption = '本地关系分类演示；未作阻塞假设。当前源不支持依赖影响结论。'
        steps = [
            {'title':'构造来源对象','mode':'objects','speech':f'现有关系足以判断依赖影响吗？先放入来源中的{len(nodes)}个对象。'},
            {'title':'保留原关系语义','mode':'relations','speech':f'按来源画出{len(edges)}条关系，并保留原始类型。建立在某项基础之上，并不自动等于执行依赖。'},
            {'title':'应用严格依赖条件','mode':'filter','speech':'现在只允许明确登记的依赖关系进入计算。其他关系仍在原图保留，不能被偷偷转换为依赖。'},
            {'title':'对照原图与依赖图','mode':'compare','speech':'左侧保留来源原图，右侧只保留依赖边。对象不变，右侧没有可用于依赖追踪的边。这是资料条件不足。'},
            {'title':'结论：影响仍然未知','mode':'conclusion','speech':'因此，这份来源不足以判断依赖影响，影响仍然未知。不能把空路径解释为没有实际影响。需要明确的依赖登记，才能继续结构演示。'}
        ]
    else:
        names = {n['id']: n['title'] for n in nodes}
        beats = [{'speech': '左侧保留假设门。右侧只恢复起点，原始关系保持不变。'}]
        for part in after['trace']:
            beats.append({'edge': part['edge'], 'speech': f'从{names[part["from"]]}反向查找，到达{names[part["to"]]}。'})
        beats.append({'speech': '所有可达对象已逐跳展开。每个对象只访问一次，循环不会重复计数。'})
        steps[3]['beats'] = beats
        steps[3]['speech'] = ''.join(b['speech'] for b in beats)
    return {'available':True, 'schema':'tfi.home-report-reasoning.v1', 'mode':mode,
            'question':'恢复一个假设禁用的起点后，结构可达范围如何变化？' if mode == 'dependency' else '现有关系足以判断依赖影响吗？',
            'nodes':nodes, 'edges':edges, 'seed':seed, 'before':before, 'after':after,
            'unconnected':unconnected, 'assumption':assumption, 'limit':limit, 'rule':rule,
            'steps':steps, 'sources':sorted({s for n in nodes for s in n['sources']}),
            'evidence_status':'structural-only' if mode == 'dependency' else 'insufficient'}
