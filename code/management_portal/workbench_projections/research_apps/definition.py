"""Emit reviewable update payloads for the two existing research Workbenches.

This command never updates resources. The integrator supplies the commit that
contains this package, saves against the listed revision, then publishes the
new revision separately after checking the returned immutable build.
"""
import argparse
import json
from pathlib import Path
import re

from management_portal.workbench_projections.development_projection import portal_source

ROOT = Path(__file__).parent

def definitions(commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('A full source commit is required')
    repo_id, directory = portal_source('management_portal/workbench_projections/research_apps')
    original = json.loads((ROOT / 'data/legacy-definitions.json').read_text())['applications']
    result = []
    for entry, old in original.items():
        bindings = json.loads(json.dumps(old['bindings']))
        bindings['main']['residents']['facade'] = bindings['main']['residents']['coordinator']
        # Patch only the migrated definition. Resource metadata such as project
        # membership belongs to ResourceService, not Workbench's update fields.
        # Existing title, owners, projects and navigation stay on the same ID.
        payload = {}
        payload.update(expected_revision=old['revision'], html='', panels={}, bindings=bindings,
            manifest={'schema':'tfi.projection.v2', 'chat':{'binding':'main','role':'facade'},
                'sidebar':[{'id':'research','title':old['title'],'icon':'table','pattern':'comparison','source':'package:'+entry}],
                'context':[{'channel':'focus','publishers':['research'],'subscribers':['research']}]},
            application={'repo_id':repo_id, 'directory':directory,
                'commit':commit,'entries':{entry:entry+'.html'}})
        result.append({'resource_id':old['workbench_id'], 'previous_published_revision':old['published_revision'], 'fields':payload})
    return result

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args=parser.parse_args()
    print(json.dumps(definitions(args.commit),ensure_ascii=False,indent=2))
