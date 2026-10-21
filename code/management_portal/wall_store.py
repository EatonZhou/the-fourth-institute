"""WALL projects, work items and reading state have separate domain tables."""
import os
from functools import lru_cache
from management_common.domain_database import DomainDatabase
from management_common.relational_rows import identifier, read_rows, sync_rows
from management_common.database_time import encode_times, decode_times

TIME_FIELDS = {'wall_jobs': ('created_at', 'completed_at', 'updated_at'),
               'wall_resident_cleanup': ('retry_at',)}

JOB_TEXT = ('action','actor','backend_route_id','block','effect_id','error','event_id','event_source_id','trigger_path_id','lineage_id','message','message_id','project','publication_commit','resident_id','resident_scope','retry_of','retry_requested_by','routing_note','source_id','status','summary','title','lineage_scope','backend','schema')
SUBMISSION_TEXT = ('request_id', 'idempotency_key', 'instruction_id', 'instruction',
                   'submission_state', 'submission_reason')
JOB_TEXT += SUBMISSION_TEXT
JOB_JSON = ('recommendation','tau_failure','operation_error','submission_error')
JOB_ARRAY = ('published_blocks','published_paths','result_block_ids','test_case_ids','test_run_ids')
TABLES = {
 'wall_projects': {'id':'TEXT PRIMARY KEY','title':'TEXT','root':'TEXT','project_id':'TEXT','automation':'JSONB','schedules':'JSONB','deleting':'BOOLEAN'},
 'wall_jobs': {'id':'TEXT PRIMARY KEY',**{k:'TEXT' for k in JOB_TEXT},**{k:'JSONB' for k in JOB_JSON},**{k:'TEXT[]' for k in JOB_ARRAY},'tau_run_id':'TEXT','created_at':'TIMESTAMPTZ','completed_at':'TIMESTAMPTZ','updated_at':'TIMESTAMPTZ','cancel_requested':'BOOLEAN','created':'BOOLEAN'},
 'wall_discussions': {'binding_key':'TEXT PRIMARY KEY','backend':'TEXT','created':'BOOLEAN','lineage_id':'TEXT','lineage_scope':'TEXT','resident_id':'TEXT','resident_scope':'TEXT','schema':'TEXT','project':'TEXT','block':'TEXT'},
 'wall_resident_cleanup': {'cleanup_key':'TEXT PRIMARY KEY','project':'TEXT','block':'TEXT','actor':'TEXT','resident_ids':'TEXT[]','removed':'BOOLEAN','attempt':'BIGINT','retry_at':'TIMESTAMPTZ','error':'TEXT'},
}
DDL = '\n'.join('CREATE TABLE IF NOT EXISTS '+table+' ('+','.join(identifier(k)+' '+v for k,v in fields.items())+');' for table,fields in TABLES.items()) + """
CREATE INDEX IF NOT EXISTS wall_job_project_status_idx ON wall_jobs(project,status);
CREATE TABLE IF NOT EXISTS wall_readers (actor TEXT NOT NULL,project TEXT NOT NULL,PRIMARY KEY(actor,project));
CREATE TABLE IF NOT EXISTS wall_reads (actor TEXT NOT NULL,project TEXT NOT NULL,block TEXT NOT NULL,revision TEXT NOT NULL,PRIMARY KEY(actor,project,block));
CREATE TABLE IF NOT EXISTS wall_reviews (actor TEXT NOT NULL,project TEXT NOT NULL,block TEXT NOT NULL,revision TEXT NOT NULL,PRIMARY KEY(actor,project,block));
CREATE TABLE IF NOT EXISTS wall_settings (name TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL);
"""
# Older instances already have wall_jobs. Add the nullable receipt fields without
# rewriting historical jobs, and avoid taking an ALTER lock on every process start.
DDL += '\n'.join("""
DO $upgrade$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_attribute
                   WHERE attrelid = 'wall_jobs'::regclass AND attname = '%s' AND NOT attisdropped) THEN
        ALTER TABLE wall_jobs ADD COLUMN %s %s;
    END IF;
END
$upgrade$;
""" % (name, identifier(name), kind)
    for name, kind in [*((name, 'TEXT') for name in SUBMISSION_TEXT), ('submission_error', 'JSONB')])


@lru_cache(maxsize=8)
def _database(dsn,namespace):
    return DomainDatabase('portal_wall','wall',DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'),os.getenv('PORTAL_DATABASE_NAMESPACE'))


def write(c,value):
    for name in ('projects','jobs','discussions','resident_cleanup'):
        table='wall_'+name
        fields=TABLES[table]
        key=next(iter(fields))
        rows=[]
        for identity,item in value.get(name,{}).items():
            if key in item and item[key]!=identity:
                raise ValueError('WALL identity mismatch')
            rows.append(encode_times({key:identity,**item}, TIME_FIELDS.get(table, ())))
        sync_rows(c,table,(key,),tuple(k for k in fields if k!=key),rows,json_columns=tuple(k for k,v in fields.items() if v=='JSONB'))
    sync_rows(c,'wall_readers',('actor','project'),(),[{'actor':a,'project':p} for a,projects in value.get('reads',{}).items() for p in projects])
    for name in ('reads','reviews'):
        sync_rows(c,'wall_'+name,('actor','project','block'),('revision',),[{'actor':a,'project':p,'block':b,'revision':v} for a,projects in value.get(name,{}).items() for p,blocks in projects.items() for b,v in blocks.items()])


class WallState:
    def transaction(self):
        return database().transaction('wall')

    def set_scheduling_enabled(self, enabled: bool) -> None:
        with self.transaction() as connection:
            connection.execute('''INSERT INTO wall_settings(name,enabled) VALUES ('scheduled_jobs',%s)
                ON CONFLICT (name) DO UPDATE SET enabled=EXCLUDED.enabled''', (enabled,))

    def read_projects(self):
        with database().read() as c:
            return {row['id']: {key: value for key, value in row.items() if value is not None}
                    for row in read_rows(c, 'wall_projects')}

    def read(self,default=None):
        with database().read() as c:
            setting = c.execute("SELECT enabled FROM wall_settings WHERE name='scheduled_jobs'").fetchone()
            value={'scheduling_enabled': bool(setting['enabled']) if setting else True}
            for name in ('projects','jobs','discussions','resident_cleanup'):
                table='wall_'+name
                key=next(iter(TABLES[table]))
                value[name]={r[key]:{k:v for k,v in decode_times(r, TIME_FIELDS.get(table, ()), format='seconds').items()
                                    if v is not None and (key=='id' or k!=key)} for r in read_rows(c,table)}
            value.update(reads={},reviews={})
            for r in read_rows(c,'wall_readers'):
                value['reads'].setdefault(r['actor'],{})[r['project']]={}
            for name in ('reads','reviews'):
                for r in read_rows(c,'wall_'+name):
                    value[name].setdefault(r['actor'],{}).setdefault(r['project'],{})[r['block']]=r['revision']
            return value

    def replace(self,value):
        with self.transaction() as c:
            write(c,value)
