"""Quality belongs to the immutable report, independently of source freshness."""
from copy import deepcopy
import math

QUALITIES = ('low', 'medium', 'high')


def quality(value=None):
    value = 'medium' if value is None else value
    if value not in QUALITIES:
        raise ValueError('quality必须为low、medium或high')
    return value


def markdown(report):
    parts = ['# ' + report['title'], report['goal'], *report.get('warnings', [])]
    for row in report['rows']:
        parts += ['## ' + row['title']]
        parts += [str(row[key]) for key in ('status', 'body', 'detail', 'conclusion', 'next') if row.get(key)]
    parts.append('## 来源与版本')
    parts += [f"- {s['label']} · {s.get('address') or s['read']} · {s['version']}" for s in report['sources']]
    return '\n\n'.join(parts) + '\n'


def fingerprint(report):
    from management_portal.home_report import digest
    return digest({k: v for k, v in report.items() if k not in ('version', 'checked_at', 'read_seconds')})


def bind(report, level=None, *, author=''):
    report = deepcopy(report)
    report.setdefault('source_version', report['version'])
    report['quality'] = quality(level)
    report['author'] = author
    report['format'] = 'markdown' if report['quality'] == 'low' else 'interactive'
    report['markdown'] = markdown(report)
    report['version'] = fingerprint(report)
    return report


def presentation(plan, changes):
    """Edit actual scene layout/narration, without inventing new source edges."""
    if not plan.get('available'):
        raise ValueError('当前来源没有可修改的交互图，请先修正来源')
    if not isinstance(changes, dict) or set(changes) - {'question','assumption','rule','limit','nodes','steps'}:
        raise ValueError('无效交互修改字段')
    result = deepcopy(plan)
    def prose(value):
        if not isinstance(value,str) or not value.strip() or len(value)>8000:
            raise ValueError('交互文字为空或过长')
        return value
    for key in ('question','assumption','rule','limit'):
        if key in changes: result[key] = prose(changes[key])
    if 'nodes' in changes:
        nodes = {node['id']:node for node in result['nodes']}
        if not isinstance(changes['nodes'],list) or len(changes['nodes'])>len(nodes):
            raise ValueError('节点修改超出固定来源范围')
        seen = set()
        for item in changes['nodes']:
            if not isinstance(item,dict) or set(item)-{'id','title','x','y'} or item.get('id') not in nodes or item['id'] in seen:
                raise ValueError('节点修改必须对应唯一固定节点')
            seen.add(item['id'])
            for key in ('x','y'):
                if key in item:
                    if isinstance(item[key],bool) or not isinstance(item[key],(int,float)) or not math.isfinite(item[key]) or not 0 <= item[key] <= 1:
                        raise ValueError('节点坐标必须在0到1之间')
                    nodes[item['id']][key] = item[key]
            if 'title' in item: nodes[item['id']]['title'] = prose(item['title'])
    if 'steps' in changes:
        if not isinstance(changes['steps'],list) or len(changes['steps']) != len(result['steps']):
            raise ValueError('分镜修改必须保留完整步骤')
        edges = {edge['id'] for edge in result['edges']}
        for original, update in zip(result['steps'],changes['steps']):
            if not isinstance(update,dict) or set(update)-{'title','speech','beats'}:
                raise ValueError('分镜仅接受标题、讲稿和同步节拍')
            for key in ('title','speech'):
                if key in update: original[key] = prose(update[key])
            if 'speech' in update: original.pop('beats',None)
            if 'beats' in update:
                beats = update['beats']
                if not isinstance(beats,list) or not 1 <= len(beats) <= 30:
                    raise ValueError('无效分镜节拍')
                for beat in beats:
                    if not isinstance(beat,dict) or set(beat)-{'speech','edge'} or ('edge' in beat and beat['edge'] not in edges):
                        raise ValueError('同步节拍必须使用固定图中的边')
                    prose(beat.get('speech'))
                original['beats'] = deepcopy(beats)
                original['speech'] = ''.join(beat['speech'] for beat in beats)
    return result


def correction(report, changes):
    if not isinstance(changes, dict) or set(changes) - {'title', 'goal', 'markdown','presentation'} or not changes:
        raise ValueError('报告修改只接受title、goal、markdown、presentation')
    result = deepcopy(report)
    for key, value in changes.items():
        if key == 'presentation':
            result['reasoning'] = presentation(result['reasoning'],value)
            result['revised_presentation'] = True
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > (100000 if key == 'markdown' else 4000):
            raise ValueError('报告修改正文为空或过长')
        result[key] = value
    result['revised_content'] = True
    result['version'] = fingerprint(result)
    return result
