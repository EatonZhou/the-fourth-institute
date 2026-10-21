"""Small typed Family fixtures for relay tests; lifecycle cases use Familize."""
import hashlib

from management_portal.society_service.state_store import get_lineage_data_store


def set_role(resident_id, *, family_id, role='facade', actor=''):
    store = get_lineage_data_store()
    def write(c):
        resident = c.execute('SELECT lineage_id FROM lineage_nodes WHERE node_id=%s', (resident_id,)).fetchone()
        c.execute("INSERT INTO families(family_id,lineage_id,title,goal,resource_id,created_at,updated_at) VALUES (%s,%s,'Test Family','',%s,'2026-10-14','2026-10-14') ON CONFLICT DO NOTHING", (family_id, resident['lineage_id'], 'family_' + hashlib.sha256(family_id.encode()).hexdigest()[:12]))
        c.execute("INSERT INTO lineage_nodes(node_id,lineage_id,node_type) VALUES (%s,%s,'function_block') ON CONFLICT DO NOTHING", (family_id,resident['lineage_id']))
        c.execute("INSERT INTO function_blocks(function_block_id,lineage_id,title,created_at,created_by,updated_at,updated_by) VALUES (%s,%s,'Test Family','2026-10-13',%s,'2026-10-13',%s) ON CONFLICT DO NOTHING", (family_id,resident['lineage_id'],actor,actor))
        c.execute("INSERT INTO function_block_elements VALUES (%s,%s,'resident',%s) ON CONFLICT DO NOTHING", (family_id,resident_id,role))
        c.execute('INSERT INTO family_residents VALUES (%s,%s,%s)', (family_id, resident_id,role))
    store.database.write(write)


def mock_family_rosters(monkeypatch, instances):
    """Supply mutable roster facts to tests of projection and speaking policy."""
    from management_portal.society_service import family_storage

    def roles(_connection, family_id):
        item = next((item for item in instances() if item['id'] == family_id), {})
        return {'member': list((item.get('family') or {}).get('members') or []),
                **{role: [item['family'][role]] if (item.get('family') or {}).get(role) else []
                   for role in ('facade', 'router', 'steward')}}

    def around(_connection, resident_id, *, max_hops=4):
        result = []
        seen = set()
        while resident_id and len(result) < max_hops:
            holder = next((item for item in instances()
                           if resident_id in sum((roles(None, item['id'])[role]
                                                  for role in ('member', 'router', 'steward')), [])), None)
            if not holder or holder['id'] in seen:
                break
            facade = roles(None, holder['id'])['facade'][0]
            result.append({'family_id': holder['id'], 'facade_id': facade})
            seen.add(holder['id'])
            resident_id = facade
        return result

    def resident_roles(_connection, resident_ids):
        result = {}
        for role in ('facade', 'router', 'steward', 'member'):
            for item in instances():
                for rid in set(resident_ids).intersection(roles(None, item['id'])[role]):
                    result.setdefault(rid, {'resident_id': rid, 'family_id': item['id'], 'role': role})
        return result

    monkeypatch.setattr(family_storage, 'roles_of', roles)
    monkeypatch.setattr(family_storage, 'families_around', around)
    monkeypatch.setattr(family_storage, 'resident_roles', resident_roles)
