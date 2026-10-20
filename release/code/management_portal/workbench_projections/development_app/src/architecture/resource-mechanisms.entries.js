// Explicit author routing; no mechanism or owner is inferred from an adapter or array position.
export const RESOURCE_MECHANISMS = Object.freeze({
  "resources.mechanism.resources": {
    "ownerId": "resources",
    "adapter": "tfi.resources.service_dispatch.v1",
    "fields": [
      "mode",
      "reachable",
      "authenticated",
      "route",
      "result"
    ]
  },
  "resources.mechanism.resources.access": {
    "ownerId": "resources.access",
    "adapter": "tfi.resources.address_resolution.v1",
    "fields": [
      "address",
      "syntax_valid",
      "authorized_now",
      "content_supported",
      "phase",
      "bytes_returned"
    ]
  },
  "resources.mechanism.resources.types": {
    "ownerId": "resources.types",
    "adapter": "tfi.resources.catalog_dispatch.v1",
    "fields": [
      "capability",
      "owner_count",
      "install_succeeds",
      "loaded",
      "registry"
    ]
  },
  "resources.mechanism.storage": {
    "ownerId": "storage",
    "adapter": "tfi.resources.commit_boundary.v1",
    "fields": [
      "domain_commit_succeeds",
      "post_commit_succeeds",
      "domain_committed",
      "reported"
    ]
  },
  "resources.mechanism.storage.workspace": {
    "ownerId": "storage.workspace",
    "adapter": "tfi.resources.pinned_file_walk.v1",
    "fields": [
      "allowed",
      "regular_no_links",
      "within_limit",
      "fd_pinned",
      "returned",
      "failure"
    ]
  },
  "resources.mechanism.storage.artifacts": {
    "ownerId": "storage.artifacts",
    "adapter": "tfi.resources.artifact_reference.v1",
    "fields": [
      "family",
      "reference_valid",
      "bytes_valid",
      "authority",
      "result"
    ]
  },
  "resources.mechanism.postgres": {
    "ownerId": "postgres",
    "adapter": "tfi.resources.transaction_ledger.v1",
    "fields": [
      "retryable_conflict",
      "retry_succeeds",
      "callback_succeeds",
      "attempts",
      "committed",
      "callback"
    ]
  },
  "resources.mechanism.resources.access.address-router": {
    "ownerId": "resources.access.address-router",
    "adapter": "tfi.resources.address_resolution.v1",
    "fields": [
      "raw",
      "valid_segments",
      "provider_available",
      "phase"
    ]
  },
  "resources.mechanism.resources.access.index": {
    "ownerId": "resources.access.index",
    "adapter": "tfi.resources.index_snapshot.v1",
    "fields": [
      "ready_after_wait",
      "visible_total",
      "offset",
      "limit",
      "phase",
      "total",
      "page_size"
    ]
  },
  "resources.mechanism.resources.access.registration": {
    "ownerId": "resources.access.registration",
    "adapter": "tfi.resources.reservation_ledger.v1",
    "fields": [
      "same_fingerprint",
      "row_readable",
      "request_a_id",
      "request_b_id",
      "result"
    ]
  },
  "resources.mechanism.resources.types.catalog": {
    "ownerId": "resources.types.catalog",
    "adapter": "tfi.resources.catalog_dispatch.v1",
    "fields": [
      "already_loaded",
      "installation_fails",
      "registry_generation",
      "loaded"
    ]
  },
  "resources.mechanism.resources.types.workspace-provider": {
    "ownerId": "resources.types.workspace-provider",
    "adapter": "tfi.resources.pinned_file_walk.v1",
    "fields": [
      "start",
      "end",
      "safe_path",
      "range_valid",
      "inode_pinned",
      "actual_start",
      "actual_end",
      "result"
    ]
  },
  "resources.mechanism.resources.types.knowledge-provider": {
    "ownerId": "resources.types.knowledge-provider",
    "adapter": "tfi.resources.revision_diff.v1",
    "fields": [
      "same_root",
      "expected_matches",
      "text",
      "domain_called",
      "outcome"
    ]
  },
  "resources.mechanism.resources.permissions.subjects": {
    "ownerId": "resources.permissions.subjects",
    "adapter": "tfi.resources.subject_lifecycle.v1",
    "fields": [
      "active",
      "binding_matches",
      "sources",
      "validated",
      "effective_sources"
    ]
  },
  "resources.mechanism.resources.permissions.policy": {
    "ownerId": "resources.permissions.policy",
    "adapter": "tfi.resources.policy_revision.v1",
    "fields": [
      "authorized_before",
      "version_matches",
      "before_version",
      "locked",
      "changed",
      "result"
    ]
  },
  "resources.mechanism.storage.workspace.locks": {
    "ownerId": "storage.workspace.locks",
    "adapter": "tfi.resources.lock_timeline.v1",
    "fields": [
      "cooperating",
      "writer_fails",
      "held",
      "b_can_enter",
      "content_replaced"
    ]
  },
  "resources.mechanism.storage.workspace.atomic-files": {
    "ownerId": "storage.workspace.atomic-files",
    "adapter": "tfi.resources.atomic_file_commit.v1",
    "fields": [
      "operation",
      "fault",
      "target_exists",
      "target_version",
      "temporary_exists",
      "temporary_version",
      "temporary_complete",
      "stream_flushed",
      "file_synced",
      "replaced",
      "directory_sync",
      "read_visible_version",
      "read_integrity",
      "result"
    ]
  },
  "resources.mechanism.storage.workspace.address-writer": {
    "ownerId": "storage.workspace.address-writer",
    "adapter": "tfi.resources.workspace_write_routes.v1",
    "fields": [
      "address_kind",
      "contributor",
      "digest_matches",
      "notify_ok",
      "directory_locked",
      "file_replaced",
      "file_fsynced",
      "route"
    ]
  },
  "resources.mechanism.storage.artifacts.effect-store": {
    "ownerId": "storage.artifacts.effect-store",
    "adapter": "tfi.resources.artifact_recovery.v1",
    "fields": [
      "scope_matches",
      "digest_size_match",
      "terminal_committed",
      "purpose",
      "usable",
      "recovery_deleted",
      "status"
    ]
  },
  "resources.mechanism.storage.artifacts.harness-store": {
    "ownerId": "storage.artifacts.harness-store",
    "adapter": "tfi.resources.scoped_output_slice.v1",
    "fields": [
      "scope_granted",
      "envelope_valid",
      "offset",
      "limit",
      "checked",
      "result"
    ]
  },
  "resources.mechanism.storage.artifacts.application-store": {
    "ownerId": "storage.artifacts.application-store",
    "adapter": "tfi.resources.artifact_receipt.v1",
    "fields": [
      "existing_digest",
      "receipt_valid",
      "file_valid",
      "action",
      "served"
    ]
  },
  "resources.mechanism.resource-access": {
    "ownerId": "resource-access",
    "adapter": "tfi.resources.envelope_inspector.v1",
    "fields": [
      "auth_valid",
      "response_id_matches",
      "stream_complete",
      "timeout",
      "dispatched",
      "accepted",
      "outcome"
    ]
  },
  "resources.mechanism.resource-authorization": {
    "ownerId": "resource-authorization",
    "adapter": "tfi.resources.policy_gate.v1",
    "fields": [
      "protected",
      "credential_allows",
      "mutates",
      "business_allows",
      "credential_gate",
      "owner_called",
      "outcome"
    ]
  },
  "resources.mechanism.database-schema": {
    "ownerId": "database-schema",
    "adapter": "tfi.resources.owner_handoff.v1",
    "fields": [
      "local_commit",
      "remote_confirmed",
      "ack_write_ok",
      "local_state",
      "outbox",
      "remote_state"
    ]
  },
  "resources.mechanism.file-storage": {
    "ownerId": "file-storage",
    "adapter": "tfi.resources.lock_revision_commit.v1",
    "fields": [
      "version_matches",
      "write_ok",
      "lock_held",
      "target",
      "result"
    ]
  },
  "resources.review.mechanism.atomic-create-only": {
    "ownerId": "storage.workspace.atomic-files",
    "adapter": "tfi.resources.create_only_file.v1",
    "fields": [
      "target_exists",
      "target_version",
      "fault",
      "temporary_version",
      "read_visible_version",
      "read_integrity",
      "file_synced",
      "directory_sync",
      "created_by_this_call",
      "result"
    ]
  },
  "resources.review.mechanism.index-refresh-coalescing": {
    "ownerId": "resources.access.index",
    "adapter": "tfi.resources.index_refresh_queue.v1",
    "fields": [
      "scenario",
      "pending",
      "pending_paths",
      "pending_force",
      "pending_full",
      "pending_rescan",
      "queued_name_count",
      "enqueue_total",
      "request_count",
      "running",
      "dispatch_paths",
      "dispatch_joined_paths",
      "dispatch_force",
      "dispatch_rescan",
      "dispatch_kind",
      "result"
    ]
  },
  "resources.review.mechanism.index-snapshot-publication": {
    "ownerId": "resources.access.index",
    "adapter": "tfi.resources.index_snapshot_publication.v1",
    "fields": [
      "scenario",
      "scan_started_generation",
      "memory_generation",
      "persisted_generation",
      "candidate_version",
      "candidate_records",
      "memory_version",
      "memory_records",
      "read_visible_version",
      "captured_reader_records",
      "durable_version",
      "dirty",
      "write_in_flight",
      "resource_error_count",
      "protected_ids",
      "direct_revisions",
      "last_error",
      "notification",
      "completion_signal",
      "phase"
    ]
  },
  "resources.mechanism.resources.permissions.coverage": {
    "ownerId": "resources.permissions.coverage",
    "adapter": "tfi.resources.permission_graph.v1",
    "fields": [
      "graph_nodes",
      "permission_edges",
      "named_private_ids",
      "derived_private_ids",
      "subject_id",
      "target_id",
      "persistent_grants_before",
      "persistent_grants_after",
      "new_grant_count",
      "credential_revision",
      "credential_set_revision",
      "credential_row_present",
      "credential_read_count",
      "graph_order",
      "processed_node_ids",
      "invalid_parent_edges",
      "unprocessed_node_ids",
      "direct_roots",
      "global_contributions",
      "node_results",
      "source_paths",
      "graph_error",
      "credential_status",
      "outcome",
      "effective_level",
      "case_input_scenario"
    ]
  },
  "resources.mechanism.resources.permissions": {
    "ownerId": "resources.permissions",
    "adapter": "tfi.resources.permission_matrix.v1",
    "fields": [
      "subject_valid",
      "graph_valid",
      "credentials_ready",
      "sufficient",
      "snapshot_open",
      "decision"
    ]
  }
});
export const resourceObjectEntries = Object.freeze({
  "postgres": "resources.mechanism.postgres",
  "resource-access": "resources.mechanism.resource-access",
  "resource-authorization": "resources.mechanism.resource-authorization",
  "database-schema": "resources.mechanism.database-schema",
  "file-storage": "resources.mechanism.file-storage",
  "resources.access.address-router": "resources.mechanism.resources.access.address-router",
  "resources.access.index": "resources.mechanism.resources.access.index",
  "resources.access.registration": "resources.mechanism.resources.access.registration",
  "resources.types.catalog": "resources.mechanism.resources.types.catalog",
  "resources.types.workspace-provider": "resources.mechanism.resources.types.workspace-provider",
  "resources.types.knowledge-provider": "resources.mechanism.resources.types.knowledge-provider",
  "resources.permissions.subjects": "resources.mechanism.resources.permissions.subjects",
  "resources.permissions.coverage": "resources.mechanism.resources.permissions.coverage",
  "resources.permissions.policy": "resources.mechanism.resources.permissions.policy",
  "storage.workspace.locks": "resources.mechanism.storage.workspace.locks",
  "storage.workspace.atomic-files": "resources.mechanism.storage.workspace.atomic-files",
  "storage.workspace.address-writer": "resources.mechanism.storage.workspace.address-writer",
  "storage.artifacts.effect-store": "resources.mechanism.storage.artifacts.effect-store",
  "storage.artifacts.harness-store": "resources.mechanism.storage.artifacts.harness-store",
  "storage.artifacts.application-store": "resources.mechanism.storage.artifacts.application-store"
});
export const resourceObjectModes = Object.freeze({
  "file-storage": [
    "resources.mechanism.file-storage",
    "resources.mechanism.storage.workspace.atomic-files",
    "resources.review.mechanism.atomic-create-only"
  ],
  "resources.access.index": [
    "resources.mechanism.resources.access.index",
    "resources.review.mechanism.index-refresh-coalescing",
    "resources.review.mechanism.index-snapshot-publication"
  ],
  "storage.workspace.atomic-files": [
    "resources.mechanism.storage.workspace.atomic-files",
    "resources.review.mechanism.atomic-create-only"
  ]
});
export const resourceModeLabels = Object.freeze({
  "resources.mechanism.postgres": "事务、重试与提交后回调",
  "resources.mechanism.resource-access": "请求信封与正文流",
  "resources.mechanism.resource-authorization": "凭证关口与领域检查",
  "resources.mechanism.database-schema": "领域提交与对端确认",
  "resources.mechanism.file-storage": "锁内校验与提交",
  "resources.mechanism.storage.workspace.atomic-files": "原子替换与读者",
  "resources.review.mechanism.atomic-create-only": "独占创建",
  "resources.mechanism.resources.access.address-router": "地址拆解与路由",
  "resources.mechanism.resources.access.index": "查询与分页",
  "resources.review.mechanism.index-refresh-coalescing": "刷新请求合并",
  "resources.review.mechanism.index-snapshot-publication": "快照发布",
  "resources.mechanism.resources.access.registration": "注册预留账本",
  "resources.mechanism.resources.types.catalog": "能力目录与安装",
  "resources.mechanism.resources.types.workspace-provider": "固定目录内的文件访问",
  "resources.mechanism.resources.types.knowledge-provider": "知识正文与版本差异",
  "resources.mechanism.resources.permissions.subjects": "执行授权主体",
  "resources.mechanism.resources.permissions.coverage": "凭证覆盖路径",
  "resources.mechanism.resources.permissions.policy": "保护集合与当前版本",
  "resources.mechanism.storage.workspace.locks": "稳定锁与合作写者",
  "resources.mechanism.storage.workspace.address-writer": "资源地址写入分支",
  "resources.mechanism.storage.artifacts.effect-store": "Effect 产物恢复",
  "resources.mechanism.storage.artifacts.harness-store": "输出切片与可见范围",
  "resources.mechanism.storage.artifacts.application-store": "内容寻址与应用包回执"
});
