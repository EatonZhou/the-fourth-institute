"""Typed node records do not expand project references into sibling content."""
import os
import uuid

import psycopg
from psycopg import sql

from management_portal.distribution.database import read_snapshot


def test_project_reference_does_not_export_sibling_node_details(monkeypatch):
    namespace = 'definition_' + uuid.uuid4().hex[:12]
    monkeypatch.setenv('PORTAL_DATABASE_NAMESPACE', namespace)
    schema = namespace + '_lineage'
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            connection.execute('CREATE TABLE lineages(lineage_id text PRIMARY KEY)')
            connection.execute('CREATE TABLE lineage_nodes(node_id text PRIMARY KEY,lineage_id text,node_type text)')
            connection.execute('CREATE TABLE residents(resident_id text PRIMARY KEY)')
            connection.execute('CREATE TABLE lineage_node_references(lineage_id text,node_id text,PRIMARY KEY(lineage_id,node_id))')
            connection.execute('CREATE TABLE lineage_resident_nodes(node_id text PRIMARY KEY)')
            connection.execute('CREATE TABLE lineage_components(node_id text PRIMARY KEY)')
            connection.execute("INSERT INTO lineages VALUES('selected'),('sibling')")
            connection.execute("INSERT INTO lineage_nodes VALUES('owned','selected','resident'),('external','sibling','resident'),('external-event','sibling','event_source')")
            connection.execute("INSERT INTO residents VALUES('owned'),('external')")
            connection.execute("INSERT INTO lineage_node_references VALUES('selected','external'),('selected','external-event')")
            connection.execute("INSERT INTO lineage_resident_nodes VALUES('owned'),('external')")
            connection.execute("INSERT INTO lineage_components VALUES('external-event')")
            with read_snapshot() as source:
                rows = source.definition_rows({'selected'})
            assert rows['lineage', 'lineage_resident_nodes'] == [{'node_id': 'owned'}]
            assert rows['lineage', 'lineage_components'] == []
            assert rows['lineage', 'residents'] == [{'resident_id': 'owned'}]
            # References remain explicit until selection prunes outside owners.
            assert len(rows['lineage', 'lineage_node_references']) == 2
        finally:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
