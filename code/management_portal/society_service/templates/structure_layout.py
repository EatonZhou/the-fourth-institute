"""Template-local topology positions and their application-time binding anchor."""
from collections import deque


def positions(body):
    found = [row for row in body.get('match', []) if row['kind'] not in {'route', 'watch', 'target'}]
    keys = [row['key'] for row in [*body.get('roles', []), *body.get('components', []), *found]]
    wires = [*body.get('wires', []), *(row for row in body.get('match', []) if row['kind'] in {'route', 'watch', 'target'})]
    sources = {row['key']: row['source'] for row in wires}
    following, fed = {key: [] for key in keys}, set()
    for row in wires:
        source, target = sources.get(row['source'], row['source']), row['target']
        if source in following and target in following and source != target:
            following[source].append(target)
            fed.add(target)
    depth = {}
    for root in [*(key for key in keys if key not in fed), *keys]:
        if root in depth:
            continue
        depth[root] = 0
        queue = deque([root])
        while queue:
            key = queue.popleft()
            for target in following[key]:
                if target not in depth:
                    depth[target] = depth[key] + 1
                    queue.append(target)
    columns = {}
    for key in keys:
        columns.setdefault(depth[key], []).append(key)
    result = {key: {'x': 18 + level * 20, 'y': 50 + (index - (len(column) - 1) / 2) * 16}
              for level, column in columns.items() for index, key in enumerate(column)}
    result.update({key: value for key, value in body.get('layout', {}).items() if key in result})
    return result


def placed(body, destination):
    layout = positions(body)
    slot = next((row['key'] for row in body.get('roles', []) if row['from'] == 'target'), None)
    origin = layout[slot] if slot else {axis: sum(p[axis] for p in layout.values()) / len(layout) if layout else 50 for axis in ('x', 'y')}
    return {key: {axis: round(destination[axis] + point[axis] - origin[axis], 2) for axis in ('x', 'y')}
            for key, point in layout.items()}
