"""Posts, replies, moderation and individual read receipts."""
import os
from functools import lru_cache
from management_common.domain_database import DomainDatabase
from management_common.relational_rows import read_rows as sql_rows, sync_rows as sql_sync
from management_common.database_time import encode_times, decode_times

TIME_FIELDS = {
    'posts': ('created_at', 'updated_at', 'edited_at', 'pinned_at'),
    'replies': ('created_at',), 'featured_posts': ('featured_at',),
    'read_receipts': ('read_at',),
}


def read_rows(c, table):
    return [decode_times(row, TIME_FIELDS.get(table, ())) for row in sql_rows(c, table)]


def sync_rows(c, table, keys, fields, rows, **kwargs):
    return sql_sync(c, table, keys, fields,
                    [encode_times(row, TIME_FIELDS.get(table, ())) for row in rows], **kwargs)

POST_FIELDS = ('title','content','category','author','created_at','updated_at','edited_at','notification_revision','mentions','pinned','pinned_at','pinned_by')
DDL = """
CREATE TABLE IF NOT EXISTS posts (post_id TEXT PRIMARY KEY,title TEXT,content TEXT,category TEXT,author TEXT,
 created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ,edited_at TIMESTAMPTZ,notification_revision BIGINT,mentions JSONB,pinned BOOLEAN,pinned_at TIMESTAMPTZ,pinned_by TEXT);
CREATE TABLE IF NOT EXISTS replies (reply_id TEXT PRIMARY KEY,post_id TEXT NOT NULL,position INTEGER NOT NULL,author TEXT,content TEXT,created_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS reply_post_idx ON replies(post_id,position);
CREATE TABLE IF NOT EXISTS featured_posts (post_id TEXT PRIMARY KEY,featured_at TIMESTAMPTZ,featured_by TEXT);
CREATE TABLE IF NOT EXISTS read_initialization (singleton INTEGER PRIMARY KEY CHECK(singleton=1));
CREATE TABLE IF NOT EXISTS read_baselines (post_id TEXT PRIMARY KEY,revision BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS readers (actor_key TEXT PRIMARY KEY,actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS read_receipts (actor_key TEXT NOT NULL,post_id TEXT NOT NULL,revision BIGINT NOT NULL,read_at TIMESTAMPTZ NOT NULL,PRIMARY KEY(actor_key,post_id));
"""


@lru_cache(maxsize=8)
def _database(dsn,namespace):
    return DomainDatabase('portal_notice','notice',DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'),os.getenv('PORTAL_DATABASE_NAMESPACE'))


def write(c,kind,value):
    if kind=='posts':
        posts = value['posts']
        sync_rows(c,'posts',('post_id',),POST_FIELDS,[{k:v for k,v in p.items() if k!='replies'} for p in posts],json_columns=('mentions',))
        sync_rows(c,'replies',('reply_id',),('post_id','position','author','content','created_at'),
                  [{**r,'post_id':p['post_id'],'position':i} for p in posts for i,r in enumerate(p.get('replies',[]))])
    elif kind=='moderation':
        sync_rows(c,'featured_posts',('post_id',),('featured_at','featured_by'),[{'post_id':k,**v} for k,v in value['featured_posts'].items()])
    elif kind=='reads':
        c.execute('INSERT INTO read_initialization VALUES (1) ON CONFLICT DO NOTHING')
        sync_rows(c,'read_baselines',('post_id',),('revision',),[{'post_id':k,'revision':v} for k,v in value['baseline_revisions'].items()])
        sync_rows(c,'readers',('actor_key',),('actor',),[{'actor_key':k,'actor':v['actor']} for k,v in value['users'].items()])
        sync_rows(c,'read_receipts',('actor_key','post_id'),('revision','read_at'),[{'actor_key':k,'post_id':p,**v} for k,user in value['users'].items() for p,v in user['posts'].items()])
    else:
        raise ValueError('Unknown notice store')


class NoticeState:
    def __init__(self,kind):
        self.kind=kind

    def transaction(self):
        return database().transaction('notice')

    def read(self,default=None):
        from .notice_board_backend import NOTICE_BOARD_SCHEMA, NOTICE_BOARD_MODERATION_SCHEMA, NOTICE_BOARD_READS_SCHEMA
        with database().read() as c:
            if self.kind=='posts':
                posts = {r['post_id']: {**r,'replies':[]} for r in read_rows(c,'posts')}
                for r in sorted(read_rows(c,'replies'),key=lambda r:r['position']):
                    posts[r['post_id']]['replies'].append({k:v for k,v in r.items() if k not in {'post_id','position'}})
                return {'schema':NOTICE_BOARD_SCHEMA,'posts':list(posts.values())}
            if self.kind=='moderation':
                return {'schema':NOTICE_BOARD_MODERATION_SCHEMA,'featured_posts':{r['post_id']:{k:v for k,v in r.items() if k!='post_id'} for r in read_rows(c,'featured_posts')}}
            if self.kind=='reads':
                if not c.execute('SELECT 1 FROM read_initialization').fetchone():
                    return default
                users={r['actor_key']:{'actor':r['actor'],'posts':{}} for r in read_rows(c,'readers')}
                for r in read_rows(c,'read_receipts'):
                    users[r['actor_key']]['posts'][r['post_id']]={'revision':r['revision'],'read_at':r['read_at']}
                return {'schema':NOTICE_BOARD_READS_SCHEMA,'baseline_revisions':{r['post_id']:r['revision'] for r in read_rows(c,'read_baselines')},'users':users}
            raise ValueError('Unknown notice store')

    def replace(self,value):
        with self.transaction() as c:
            write(c,self.kind,value)
