"""Read-only relational snapshots and portable SQL from selected rows.

Credential columns are never read; only public User IDs enter anonymization.
Definitions and historical rows have
separate selectors; a Project selection cannot expand into its contents.
"""
from __future__ import annotations

import os
import hashlib
import json
import re
import shutil
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from management_common.portal_database import _database_schema, portal_database_url
from .sanitize import scrub

SCHEMAS = ("lineage", "agent", "resident_context", "resource_registry", "workspace", "templates", "permission", "workbenches", "execution_accounts", "article")
STRUCTURE = {
    "lineages", "project_lineage_entries", "lineage_layout_positions", "lineage_nodes", "lineage_node_references",
    "lineage_components", "lineage_resident_nodes", "lineage_edges", "lineage_observation_targets", "function_blocks",
    "function_block_elements", "lineage_fragments", "lineage_fragment_attachments", "families", "family_residents",
    "residents", "resident_execution_configuration", "resident_derivations", "resident_workspace_bindings",
}
NEVER_ROWS = {"resident_resource_projection_outbox", "instruction_executions", "instruction_retry_guards",
              "worker_lease", "resident_workspace_transfers", "resident_lifecycle_operations", "effect_recovery_cleanup"}
METADATA = {"lineage_meta", "agent_store_meta", "registry_meta"}


@dataclass
class Table:
    schema: str
    name: str
    columns: dict
    primary: tuple


class ReferenceClosureError(ValueError):
    def __init__(self, missing):
        self.missing_references = missing
        super().__init__('Export has missing foreign-key references: ' + json.dumps(missing, ensure_ascii=False))


class DatabaseSnapshot:
    def __init__(self, connection):
        self.connection = connection
        self.tables = {}
        self.schemas = {}
        names = {row["schema_name"] for row in connection.execute("SELECT schema_name FROM information_schema.schemata")}
        for logical in SCHEMAS:
            actual = _database_schema(logical)
            if actual not in names:
                continue
            self.schemas[logical] = actual
            columns = connection.execute("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name,ordinal_position", (actual,)).fetchall()
            for row in columns:
                key = (logical, row["table_name"])
                self.tables.setdefault(key, Table(logical, row["table_name"], {}, ())).columns[row["column_name"]] = row["data_type"]
            keys = connection.execute("""SELECT tc.table_name,kcu.column_name FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu USING(constraint_catalog,constraint_schema,constraint_name)
                WHERE tc.table_schema=%s AND tc.constraint_type='PRIMARY KEY' ORDER BY kcu.ordinal_position""", (actual,)).fetchall()
            for key in keys:
                self.tables[(logical, key["table_name"])].primary += (key["column_name"],)

    def rows(self, schema, table, *, predicates=None):
        if (schema, table) not in self.tables:
            return []
        query = sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(self.schemas[schema]), sql.Identifier(table))
        values = []
        if predicates is not None:
            clauses = []
            for column, accepted in predicates.items():
                accepted = sorted(set(accepted))
                if column in self.tables[(schema, table)].columns and accepted:
                    clauses.append(sql.SQL("{}::text = ANY(%s)").format(sql.Identifier(column)))
                    values.append(accepted)
            if not clauses:
                return []
            query += sql.SQL(" WHERE ") + sql.SQL(" OR ").join(clauses)
        return self.connection.execute(query, values).fetchall()

    def read_index(self, *, resource_ids=None, resource_type=None, query=''):
        # The index is a projection, used only to locate explicitly selected content.
        logical = os.getenv("PORTAL_RESOURCE_DATABASE_SCHEMA") or ("resources" if os.getenv("PORTAL_DATABASE_NAMESPACE") else "resource_dev")
        schema = _database_schema(logical)
        present = self.connection.execute("SELECT to_regclass(%s) AS name", (schema + ".resource_index_records",)).fetchone()
        if not present["name"]:
            return {}
        clauses, values = [], []
        if resource_ids is not None:
            identities = sorted(set(resource_ids))
            if not identities:
                return {}
            clauses.append(sql.SQL('resource_id = ANY(%s)'))
            values.append(identities)
        if resource_type:
            clauses.append(sql.SQL("record_json::jsonb->>'resource_type' = %s"))
            values.append(resource_type)
        if query:
            clauses.append(sql.SQL("strpos(lower(resource_id || ' ' || coalesce(record_json::jsonb->>'title','')), lower(%s)) > 0"))
            values.append(query)
        statement = sql.SQL("SELECT resource_id,record_json FROM {}.resource_index_records").format(sql.Identifier(schema))
        if clauses:
            statement += sql.SQL(' WHERE ') + sql.SQL(' AND ').join(clauses)
        rows = self.connection.execute(statement, values)
        records = {row["resource_id"]: json.loads(row["record_json"]) if isinstance(row["record_json"], str) else row["record_json"] for row in rows}
        from .candidate_records import current_records
        return current_records(self, records)

    def source_identity_names(self):
        """Read public User identities, never authentication fields or grants."""
        if hasattr(self, "_source_identity_names"):
            return list(self._source_identity_names)
        names = set(self.read_index(resource_type="user"))
        # Identity authority, not index completeness, defines the human set.
        # Select only public primary keys on the same readonly SQL snapshot.
        schema = _database_schema("portal_identity")
        for table, column in (("user_accounts", "username"), ("directory_users", "id")):
            if not self.connection.execute("SELECT to_regclass(%s) AS name", (schema+"."+table,)).fetchone()["name"]:
                continue
            query = sql.SQL("SELECT {} AS identity FROM {}.{}").format(sql.Identifier(column), sql.Identifier(schema), sql.Identifier(table))
            names.update(row["identity"] for row in self.connection.execute(query))
        self._source_identity_names = tuple(sorted(names))
        return list(self._source_identity_names)

    def installation_site_config(self):
        from management_common.site_config import read_site_config
        from .native_switches import minimal_site_config
        return minimal_site_config(read_site_config())

    def definition_rows(self, lineage_ids):
        output = {}
        known = set(lineage_ids)
        pending = set(STRUCTURE)
        while pending:
            changed = False
            for name in sorted(pending):
                table = self.tables.get(("lineage", name))
                if not table:
                    pending.remove(name)
                    continue
                predicates = {key: known for key in ("lineage_id", "resident_id", "node_id", "function_block_id", "family_id", "edge_id", "fragment_id") if key in table.columns}
                rows = self.rows("lineage", name, predicates=predicates)
                rows = [row for row in rows if not row.get("deleted_at") and (not row.get("lineage_id") or row["lineage_id"] in lineage_ids)]
                if "resident_id" in table.columns:
                    owned = {row["node_id"] for row in output.get(("lineage", "lineage_nodes"), []) if row.get("node_type") == "resident"}
                    rows = [row for row in rows if row["resident_id"] in owned]
                previous = len(output.get(("lineage", name), []))
                output[("lineage", name)] = rows
                if len(rows) > previous:
                    for row in rows:
                        known.update(row[key] for key in table.primary if isinstance(row.get(key), str) and row[key])
                    changed = True
            if not changed:
                break
        active = {row['resident_id'] for row in output.get(('lineage', 'residents'), [])}
        stale = {row['node_id'] for row in output.get(('lineage', 'lineage_nodes'), [])
                 if row.get('node_type') == 'resident' and row['node_id'] not in active}
        for key, rows in output.items():
            output[key] = [row for row in rows if not any(row.get(field) in stale for field in
                          ('resident_id', 'node_id', 'element_id', 'source_node_id', 'target_node_id'))]
        # A project reference can introduce a sibling node ID into discovery,
        # but does not select that node's owning Lineage or its typed details.
        owned_nodes = {row['node_id'] for row in output.get(('lineage', 'lineage_nodes'), [])}
        for name in ('lineage_resident_nodes', 'lineage_components'):
            key = ('lineage', name)
            if key in output:
                output[key] = [row for row in output[key] if row['node_id'] in owned_nodes]
        return output

    def historical_rows(self, resident_ids, lineage_ids):
        output = {}
        def take(schema, name, **fields):
            result = self.rows(schema, name, predicates=fields)
            output[schema, name] = result
            return result
        def values(rows, field):
            return {r[field] for r in rows if isinstance(r.get(field), str) and r[field]}
        # Direct business ownership takes precedence over reused message IDs.
        for (schema, name), table in self.tables.items():
            if schema not in {'lineage', 'resident_context'} or (schema == 'lineage' and name in STRUCTURE) or name in NEVER_ROWS | METADATA:
                continue
            if 'resident_id' in table.columns:
                take(schema, name, resident_id=resident_ids)
            elif 'lineage_id' in table.columns:
                take(schema, name, lineage_id=lineage_ids)
        paths = values(output.get(('lineage', 'trigger_paths'), []), 'trigger_path_id')
        steps = take('lineage', 'trigger_path_steps', trigger_path_id=paths)
        take('lineage', 'trigger_path_step_edges', trigger_path_id=paths)
        take('lineage', 'trigger_path_execution_links', step_id=values(steps, 'step_id'))
        agents = take('agent', 'agents', agent_id=resident_ids)
        owned_agents = set(resident_ids)
        while True:
            children = self.rows('agent', 'agents', predicates={'parent_agent_id': owned_agents})
            added = values(children, 'agent_id') - owned_agents
            if not added:
                break
            agents.extend(row for row in children if row['agent_id'] in added)
            owned_agents.update(added)
        executions = take('agent', 'executions', agent_id=owned_agents)
        operations = take('agent', 'agent_operations', agent_id=owned_agents)
        sequences = values(agents, 'trace_sequence_id')
        take('agent', 'sequences', sequence_id=sequences)
        positions = take('agent', 'sequence_segments', sequence_id=sequences)
        segments = take('agent', 'segments', segment_id=values(positions, 'segment_id'), created_by_agent_id=owned_agents)
        # A copied/forked segment may refer to a source segment, but never use
        # its identity to select other agents, sequences or provider sessions.
        segment_ids = values(segments, 'segment_id')
        take('agent', 'segment_relations', source_segment_id=segment_ids, target_segment_id=segment_ids)
        take('agent', 'payloads', payload_ref=values(segments, 'content_ref'))
        take('agent', 'execution_inputs', execution_id=values(executions, 'execution_id'))
        take('agent', 'operation_positions', sequence_id=sequences)
        take('agent', 'chat_message_positions', sequence_id=sequences)
        take('agent', 'chat_actor_activity', sequence_id=sequences)
        return output

    def prepare_references(self, selected, archive):
        """Validate every real FK before writing SQL; detach known control refs."""
        constraints = self.connection.execute('''SELECT sn.nspname AS source_schema,sc.relname AS source_table,c.conname,
            tn.nspname AS target_schema,tc.relname AS target_table,
            ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n,o) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o) AS source_columns,
            ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n,o) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n ORDER BY k.o) AS target_columns,
            ARRAY(SELECT a.attnotnull FROM unnest(c.conkey) WITH ORDINALITY k(n,o) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o) AS not_null
            FROM pg_constraint c JOIN pg_class sc ON sc.oid=c.conrelid JOIN pg_namespace sn ON sn.oid=sc.relnamespace
            JOIN pg_class tc ON tc.oid=c.confrelid JOIN pg_namespace tn ON tn.oid=tc.relnamespace
            WHERE c.contype='f' AND sn.nspname=ANY(%s) ORDER BY sn.nspname,sc.relname,c.conname''', (list(self.schemas.values()),)).fetchall()
        logical = {actual: name for name, actual in self.schemas.items()}
        repairs, missing = [], []
        for constraint in constraints:
            source = (logical.get(constraint['source_schema'], constraint['source_schema']), constraint['source_table'])
            target = (logical.get(constraint['target_schema'], constraint['target_schema']), constraint['target_table'])
            columns = constraint['source_columns']
            rows = selected.get(source, [])
            # Execution history remains; a source provider session is a live
            # control binding. Its real owner schema explicitly permits NULL.
            if source == ('agent', 'executions') and target == ('agent', 'provider_sessions') and columns == ['provider_session_ref']:
                if any(constraint['not_null']):
                    raise ValueError('Source execution provider session is not nullable; cannot detach safely')
                count = 0
                for row in rows:
                    if row.get('provider_session_ref') is not None:
                        archive.append({'source_schema':source[0], 'source_table':source[1], 'row':deepcopy(row)})
                        row['provider_session_ref'] = None
                        count += 1
                repairs.append({'table':'.'.join(source), 'column':'provider_session_ref', 'action':'detach_nullable_source_control', 'rows':count})
            targets = {tuple(row.get(column) for column in constraint['target_columns']) for row in selected.get(target, [])}
            absent = sum(1 for row in rows if all(row.get(column) is not None for column in columns)
                         and tuple(row.get(column) for column in columns) not in targets)
            if absent:
                missing.append({'constraint':constraint['conname'], 'table':'.'.join(source), 'target':'.'.join(target), 'columns':columns, 'missing_rows':absent})
        if missing:
            raise ReferenceClosureError(missing)
        return {'foreign_keys_checked':len(constraints), 'missing_foreign_keys':[], 'detached_control_references':repairs}

    def dump(self, selected, *, mappings=(), frozen_history=()):
        """Schema and content use the same read-only exported PostgreSQL snapshot."""
        binary = shutil.which("pg_dump")
        if not binary:
            candidate = os.path.expanduser("~/.local/postgresql-16/usr/lib/postgresql/16/bin/pg_dump")
            if os.path.isfile(candidate):
                binary = candidate
        if not binary:
            raise RuntimeError("PostgreSQL pg_dump is required")
        snapshot_id = self.connection.execute("SELECT pg_export_snapshot() AS id").fetchone()["id"]
        # pg_dump's default connection does not expand a conninfo-valued
        # PGDATABASE. Translate libpq parameters without secrets in argv.
        from psycopg.conninfo import conninfo_to_dict
        env = {**os.environ}
        for key, value in conninfo_to_dict(portal_database_url()).items():
            env[{"dbname": "PGDATABASE", "connect_timeout": "PGCONNECT_TIMEOUT"}.get(key, "PG" + key.upper())] = value
        if "/.local/postgresql-16/" in binary:
            library = os.path.expanduser("~/.local/postgresql-16/usr/lib/x86_64-linux-gnu")
            env["LD_LIBRARY_PATH"] = library + ":" + env.get("LD_LIBRARY_PATH", "")
        def ddl(section):
            command = [binary, "--schema-only", "--no-owner", "--no-privileges", "--no-comments", "--snapshot", snapshot_id, "--section", section]
            for actual in self.schemas.values():
                command.extend(["--schema", actual])
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            if result.returncode:
                diagnostic = scrub(result.stderr.replace(portal_database_url(), "[configured database]"))[-800:]
                raise RuntimeError("Source schema export failed (pg_dump): " + diagnostic)
            # pg_dump >=16.10 emits psql client guard commands, not SQL.
            # No arbitrary client directives are accepted in a distribution.
            return "\n".join(line for line in result.stdout.splitlines()
                             if not line.startswith(("\\restrict ", "\\unrestrict ")))
        # AgentData payload bodies are typed UTF-8 JSON/text, not opaque auth blobs.
        from .privacy import source_users
        source_usernames = sorted(set(source_users(selected)) | set(self.source_identity_names()))
        payload_hashes = {}
        for row in selected.get(("agent", "payloads"), []):
            body = bytes(row["body"])
            try:
                text = body.decode("utf-8")
                try:
                    text = json.dumps(scrub(json.loads(text), mappings=mappings, source_usernames=source_usernames), ensure_ascii=False, separators=(",", ":"))
                except json.JSONDecodeError:
                    text = scrub(text, mappings=mappings, source_usernames=source_usernames)
                body = text.encode()
            except UnicodeDecodeError:
                raise ValueError("Historical AgentData payload is not auditable text")
            row["body"] = body
            row["sha256"] = hashlib.sha256(body).hexdigest()
            row["byte_length"] = len(body)
            payload_hashes[row["payload_ref"]] = row["sha256"]
        for row in selected.get(("agent", "segments"), []):
            if row.get("content_ref") in payload_hashes:
                row["content_sha256"] = payload_hashes[row["content_ref"]]
        pieces = [ddl("pre-data")]
        pieces.append("CREATE SCHEMA distribution_history; CREATE TABLE distribution_history.records(sequence BIGINT PRIMARY KEY,source_schema TEXT NOT NULL,source_table TEXT NOT NULL,original_row JSONB NOT NULL);")
        for sequence, item in enumerate(frozen_history, 1):
            historical = json.loads(json.dumps(scrub(item["row"], mappings=mappings, source_usernames=source_usernames), default=str))
            pieces.append(sql.SQL("INSERT INTO distribution_history.records VALUES ({},{},{},{});").format(
                sql.Literal(sequence), sql.Literal(item["source_schema"]), sql.Literal(item["source_table"]), sql.Literal(Jsonb(historical))).as_string(self.connection))
        if ("execution_accounts", "backend_roles") in self.tables:
            # New-site roles retain their names while waiting for a new account.
            pieces.append("ALTER TABLE execution_accounts.backend_roles ALTER COLUMN account_id DROP NOT NULL;")
        for (schema, name), rows in sorted(selected.items()):
            if not rows:
                continue
            if (schema, name) == ('agent', 'chat_actor_activity'):
                # Owner semantics: one latest activity per sequence/actor.
                # Human anonymization merges actors, not messages or history.
                merged = {}
                for row in rows:
                    clean_actor = scrub(row['actor'], field='actor')
                    key = (row['sequence_id'], clean_actor)
                    if key not in merged or row['latest_at'] > merged[key]['latest_at']:
                        merged[key] = {**row, 'actor': clean_actor}
                rows = list(merged.values())
            table = self.tables[(schema, name)]
            for row in rows:
                values = []
                for column, value in row.items():
                    if column.endswith('_json') and isinstance(value, str):
                        value = json.loads(value)
                    clean = scrub(value, mappings=mappings, field=column.removesuffix("_json"), source_usernames=source_usernames)
                    if isinstance(clean, (dict, list)) and table.columns[column] in {'text', 'character varying'}:
                        clean = json.dumps(clean, ensure_ascii=False, separators=(',', ':'))
                    if clean is None and value is not None and table.columns[column] in {"text", "character varying"}:
                        clean = ""
                    if table.columns[column] in {"json", "jsonb"} and clean is not None:
                        clean = Jsonb(clean)
                    values.append(sql.Literal(clean))
                query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({});\n").format(
                    sql.Identifier(schema), sql.Identifier(name), sql.SQL(",").join(map(sql.Identifier, row)), sql.SQL(",").join(values))
                pieces.append(query.as_string(self.connection))
        pieces.append(ddl("post-data"))
        result = "\n".join(pieces)
        for logical, actual in sorted(self.schemas.items(), key=lambda item: len(item[1]), reverse=True):
            if logical != actual:
                result = re.sub(r"\b" + re.escape(actual) + r"\b", logical, result)
        return result


@contextmanager
def read_snapshot():
    with psycopg.connect(portal_database_url(), row_factory=dict_row) as connection:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield DatabaseSnapshot(connection)
