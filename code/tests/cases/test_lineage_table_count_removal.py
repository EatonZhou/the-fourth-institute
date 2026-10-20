"""Current Lineage storage has no write-amplifying row counters.

:module: spec.design-governance
"""
from management_portal.society_service.state_store import get_lineage_data_store

def test_schema_has_no_counter_tables_or_triggers():
    """Lineage 数据库 schema 中没有 lineage_table_counts 表和计数触发器"""
    with get_lineage_data_store().database.read() as c:
        assert c.execute("SELECT to_regclass(current_schema() || '.lineage_table_counts') value").fetchone()["value"] is None
        assert c.execute("SELECT count(*) n FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal AND position('count' in t.tgname)>0").fetchone()["n"] == 0
