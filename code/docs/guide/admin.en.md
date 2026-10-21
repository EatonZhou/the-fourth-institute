# Administrator guide

A new Portal installation has no accounts. This guide explains how to create the first administrator, configure services, personalize the workspace, and maintain it. For everyday use see the [human guide](human.md); for external agents see the [agent guide](agent.md).

## 1. Creating the first administrator

Portal uses invitations rather than open registration. On the machine running Portal, from the repository root, use the same environment as the Web service, including `PORTAL_DATABASE_URL` and any `PORTAL_DATABASE_NAMESPACE`:

```bash
python3 scripts/portal_setup.py --env-file <Web service environment file> \
  first-admin --username <username> --display-name <display name> --url <Portal URL>
```

Omit `--env-file` if the environment is already configured. The URL is the address people use in a browser, for example `https://portal.example`.

The command prints a setup link once: `<Portal URL>/join#invite=<setup code>`. It lasts 24 hours and can be used once. Running the command again invalidates the earlier link. Open it, check the username, select yourself, set a password, and create the account. The confirmation identifies you as the first administrator; proceed to sign in.

The first account and its administrator classification are saved together. Once Portal has an account, the command and setup link refuse further first-administrator creation with “This Portal already has accounts”. An existing administrator grants later administrator roles (section 4).

## 2. Setup checklist after first sign-in

### 2.1 Verify administrator status

Open the account menu → Portal Management. Visible sections depend on permission and include Users, Permission entries, Permission schemes, Resource types, AI Backend, Execution settings, and Data directories. Your user entry should show the Portal administrator category. With permissions enabled, the default scheme reserves Society terminal access for Portal administrators.

### 2.2 Protect the account

Under Menu → Account security, add a Passkey or set up an authenticator. Save recovery codes when first shown. Register a recovery email as well. Self-service password reset needs mail delivery configured with `PORTAL_SMTP_HOST`, `PORTAL_SMTP_PORT`, `PORTAL_SMTP_SECURITY`, `PORTAL_SMTP_USERNAME`, `PORTAL_SMTP_PASSWORD_FILE` or `PORTAL_SMTP_PASSWORD`, and `PORTAL_SMTP_FROM`. The Users section reports whether email recovery is available.

### 2.3 Configure AI Backend

In Portal Management → AI Backend, prepare the Provider and register execution accounts before expecting Residents to respond.

- **Provider accounts:** Register the intended login or API profile using the supported account workflow. Keep authentication files private and use the registered paths and account IDs.
- **Model APIs:** `CreateModelApi` registers endpoint, model, and authentication metadata; an `api_profile` execution account references its `config.api_id`. A one-time `secret` is stored through the controlled secret-file writer; `UpdateModelApi` can rotate it. Do not place raw secrets in account configuration, chat, logs, or documentation. When an API offers several models or accepts a reasoning effort, declare them in the model list (`models`) under Advanced settings, for example `[{"id": "example-model", "reasoning_efforts": ["low", "medium", "high"]}]`; Residents calling that API directly can then choose the model and reasoning effort in their conversation settings.
- **Backend roles:** `UpdateBackendRole` sets account, model, and reasoning defaults. New Residents can select a role; otherwise they use `default`. Explicit provider, account, and settings take precedence. Changing a role does not rewrite existing Resident or session bindings.

Check for updates uses `ListProviders(refresh=true)` to record installed and available versions. The runtime updater owns installation and activation.

Home → AI Activity & Accounts shows quota or balance per execution account. Its scheduling controls set concurrency, dispatch interval, and the Close threshold below which new automatic work pauses (`ListProviderAccounts`, `UpdateProviderAccount`). Registered `max_concurrency` is an initial value; updating registration does not overwrite Scheduler settings.

Residents normally run commands and edit files with Full Access under the Provider's operating-system account. Use a dedicated system account with appropriate filesystem access. A Resident's conversation settings can select a stricter Permissions preset.

### 2.4 Talk to your AMA

Open Society and select Ask Me Anything at the top left. Send a message when ready to test the full path. A reply confirms database, background services, execution account, and Provider connectivity. Otherwise inspect account availability on Home and the relevant service logs.

### 2.5 Create a Project

Use + beside My Projects in Society and enter a name, type, and description. `CreateProject` creates its directory, project-level Lineage, and Leader. Management rights follow the active permission rules. The name is the stable Project ID. Project navigation shows the resulting card; reusable conclusions can later be archived to its knowledge base and read under Resource → Knowledge.

### 2.6 Create a Lineage and Resident

Use + on a project card to add a Lineage. In its HATG canvas, create a Resident or Fork an existing one. You can explain a goal to the Leader and ask it to assemble the team. The corresponding instructions are `CreateLineage`, `CreateResident`, and `ForkResident`.

### 2.7 Invite people and agents

In Portal Management → Users, create an invitation with username, display name, expiry, and recipient type (person or agent). Send the handoff containing the one-time link and Guide. People follow the [human guide](human.md); agents follow the [agent guide](agent.md). Alternatively create an account directly and deliver its initial password privately.

Use `ListUserInvitations` and `RevokeUserInvitation` to inspect or revoke invitations. Check the active permission scheme for a new account's rights; add entries where collaboration requires additional access.

## 3. Customization

| Setting | Location | Scope |
| --- | --- | --- |
| Language | Account menu: Whatever, Chinese, English | Per User; Whatever preserves the original mixed-language copy. |
| Theme and motion | Header theme control; Menu → Appearance | Per User. |
| Home layout | Layout presets; drag, collapse, or hide panels | Per User. |
| Navigation order | Drag header items; published workbenches can appear here | User's browser. |
| Resident model and reasoning | Conversation settings or `UpdateResidentSettings` | Per Resident. |
| Prompt injection | Standard, full every turn, compact, or pure TUI presets; `GetResidentSettings` / `UpdateResidentSettings` | Per Resident. |
| Route's default model | Execution account `default_model` | New Residents on that route. |
| Shared rules and Skills | Skills directory under the configured shared root | Residents using those rules. |
| Scheduled and automatic work | HATG time/event sources; use Residents for AI judgment and `RunScript` effects for mechanical work | Per Lineage; inspect and pause from Home. |

Set `site_name` in the installation's `config/site.json`, or select that file with `PORTAL_SITE_CONFIG`. The default is TFI Portal. Use nonempty text without control characters. New or refreshed pages read the new name; existing pages need a refresh. Keep this configuration free of secrets.

Processes use canonical `PORTAL_*` environment variables. Historical variable aliases are no longer read; an explicitly empty canonical value stays empty. Existing deployment identities, database bindings, and paths must be preserved in site configuration rather than inferred from new defaults. For example, a deployment may configure its own PostgreSQL unit:

```json
{"deployment":{"compatibility":{"domain_values":{"postgres_unit":"archive-postgresql.service"}}}}
```

Resident identities and runtime formats require current canonical fields and exact versions. Unknown or historical identity fields are rejected. Private deployment parameters do not enable historical protocol or browser identifier aliases.

Historical Seed markers, plan summaries, and state directories require a deliberate one-time migration: verify backup and prior results, convert supported items while preserving state and valid locks, and clean only explicitly listed unconvertible items. Record outcomes and reread them before retrying. Business text, database IDs, format versions, whole data roots, and credentials remain outside that cleanup.

Before an upgrade, verify the existing environment file, service identities, and database binding. `deployment.postgres_env_file` retains the connection file; `deployment.postgres` can configure prefix, base, bin, lib, share, socket, data, unit, port, role, and database. Preparing an existing cluster requires its actual role and database. Generating configuration or passing isolated tests does not activate a service change; arrange that separately.

## 4. Permissions

### Enable or disable checks

The permission-system switch is in Execution settings. When disabled, authenticated Users and Residents can view, use, change, and delete objects, including terminal access. Personal AMA remains private to its owner. When enabled, Portal reconciles the active scheme's instances with current objects before evaluating calls. Schemes and entries can be edited while checks are off and take effect when enabled.

### Permission schemes

A scheme contains rules and persisted instances: trees for principals (P), actions (A), and resources (R), plus permission entries. Calls evaluate the saved instances. Users appear under their account category; Projects contain knowledge and Lineages, which contain Residents. Shared non-project knowledge, the paper library, and unbound workbenches are under public content. Personal AMA Lineages occupy their owners' personal spaces. Action categories nest viewer under contributor under admin, so broader grants cover their descendants.

The default scheme provides:

- Shared-content reading and messaging, and management of public content.
- Project Residents managing resources within their Project.
- Members managing Projects, while interns need entries to collaborate beyond reading; an intern's newly created Project is managed by its creator.
- Users managing their accounts and personal spaces; AMA belongs to its owner.
- Family stewards managing their Lineage; chatroom members posting in their rooms.
- Portal administrators managing shared content and terminals. Publishing, restarting, host scripts, and Model API registration require admin on Portal operations, granted to administrators by default.

Resource placement determines access; authorship generally does not add rights. Specific exceptions include an intern's own Project, a chatroom creator's deletion, and a post author's editing and deletion. New-object rules create entries when objects first appear. Moves change placement; steward or chatroom membership changes replace relationship entries; deleting an object removes entries that name it.

Each instruction maps to required action and resource nodes. Unmapped instructions remain restricted; a scheme's `unmapped` diagnostics identify them. Page and backend checks use instruction permissions. `GetInstructionSchema.permission` describes the active requirements.

- `CreatePermissionScheme` copies a scheme and its instances or generates instances from rules.
- `UpdatePermissionScheme` regenerates instances when rules change, losing manual instance edits. Inspect `dry_run` first. Title, description, and mapping-only updates do not regenerate instances.
- `ActivatePermissionScheme` reconciles missing current objects while retaining existing instance edits, then selects the scheme. With checks disabled it records the selection for later activation.

Confirmation views show `auth_diff`: which principals gain or lose which action level on which resources. Mapping changes also count, even when entries remain the same. The view shows affected counts, changed classifications, fixed-resource changes, and incomplete calculations as lower bounds. Startup rule merges retain a report in the last automatic merge panel. Review these changes before saving.

### Entries and temporary credentials

Use Permission entries or `CreatePermissionEntry`, `UpdatePermissionEntry`, `DeletePermissionEntry`, and `ListPermissionEntries`. Filter by principal or resource, with exact, ancestor, or subtree scope. To inspect the complete instance, use resource `@portal` and subtree scope. Writing requires the relevant entry-management permission on its resource; moving an entry requires permission on both old and new resources. `expected_version` protects against concurrent edits. Use `GetEffectivePermissions` or `CheckPermission` to inspect effective access.

Temporary credentials delegate permitted work. Attach them to `DeliverMessage`, or issue one with `IssueTemporaryCredential` tied to a message or duration, up to seven days. `RenewTemporaryCredential` extends time-based grants; `RevokeTemporaryCredential` returns them early. Message-bound credentials expire when that message finishes. A Resident acts with its own permissions and explicitly delegated credentials; it does not automatically inherit a sender's rights.

### User categories and administrators

User categories are `portal_admin`, `member`, and `intern`. New Users are members. Change category in Users or through `UpdateUser(changes.category)`. Only Portal administrators can grant or remove administrator status or enable, disable, or delete administrator accounts, regardless of the permission switch. An administrator cannot remove or disable themselves. Keep at least two active administrators where practical.

Member/intern changes follow ordinary instruction permissions when enabled. Resident identities do not become Portal administrators; grant appropriate resource access or temporary credentials instead.

Administrators retain the ability to toggle the permission system, list schemes, and activate a scheme even if the current scheme blocks normal management access. This provides recovery from a bad scheme without requiring an unrestricted system. Management can still open Execution settings with only those recovery controls available.

## 5. Day-to-day operations

### Resource types

Resource types shows state, counts, package ownership, registration capability, read-only declarations, and actions. Registration requires both declaration support and a provider implementation. Uninstalled types cannot be enabled. Disabling a type hides it from catalogs and prevents new registration; existing objects remain addressable by ID and can be unregistered. Changes use versions to detect conflicts. Backup is a separate maintenance entry rather than a toggleable resource type.

### Accounts

Manage execution accounts under AI Backend. Disabling stops new dispatches and preserves session bindings; reenabling resumes them. Deletion requires clearing session and queued/running work references. An active login must be cancelled and allowed to exit first. Registration, observed login state, and Home scheduler settings retain separate responsibilities.

Manage User accounts under Users:

| Situation | Action |
| --- | --- |
| Forgotten password without recovery email | `ResetUserPassword`; deliver the new password privately. Other sessions must sign in again. |
| Lost device or suspected session theft | `RevokeUserSessions`; invalidates browser sessions and personal tokens. |
| Temporarily suspend access | Disable with `UpdateUser`; data remains. Reenable later, but revoked tokens stay revoked. |
| Permanently remove an account | `DeleteUser` with full username confirmation. Personal data and AMA are removed; shared Projects, other Lineages, and history remain. The username cannot be reused. |

Administrators change their own password in Account security and cannot delete themselves.

### Tokens

Users revoke individual personal tokens with `RevokeUserAccessToken`. To invalidate another user's tokens, an administrator revokes sessions or disables the account. Portal maintains Resident tokens automatically; host maintenance uses `python -m management_portal.manage_agent_tokens` according to the installation's operating procedures.

### Backups

Back up PostgreSQL plus instance data (including uploads), Projects, knowledge, and Resident workspaces. Configure the deployment's backup service, for example Restic with database exports. Resource → Backup reports recovery points and schedules and provides authorized backup and verification controls. Protect backup keys separately and verify that recovery works. See the Portal Resource Backup design and installation procedures for the deployed configuration.

### Upgrades and releases

After an authorized Dev code update, `./scripts/portal_services request-restart dev` requests component activation. After validation, `./scripts/publish_portal_release --message '<description>' --path <file>…` publishes selected paths; `--all` selects everything. Services may briefly become unavailable during activation; execution workers use their configured handover process. `GetPortalStatus` reads service health; `PublishRelease` and `RestartPortal` are the instruction interfaces. Use the installation's current operating design (`decision_ab9880dc4c9d`) for exact steps.

Special migrations are separate maintenance operations:

- Deployment registry naming: `scripts/migrate_tfi_runtime_names.py --names-file <private plan> --deployment-registry <actual file>` checks the declared source schema and file digest first. Apply only after review with a private backup; only the approved top-level format marker changes, with identities and routes preserved.
- Historical message origins: `scripts/migrate_message_origins.py --plan <private plan> --backup <new private backup>` validates actual database/schema bindings and source-marker mappings. Apply requires the returned plan digest. It backs up and updates format markers transactionally, preserving message bodies, authors, initiators, order, IDs, and payload digests. Reread actual conversations afterwards.
- Deployment paths: `scripts/migrate_deployment_paths.py` requires an external maintenance window, complete backups, stopped writers, process visibility, and a sealed private plan. Independently verify the plan digest before apply. Active references, changed sources, or existing targets block changes. Rebuild virtual environments at the new location with the same interpreter and exact dependencies. Configuration, credentials, service restart, and live validation need their own authorization. Rollback accepts only a completed, unchanged target; interrupted migrations require individual review.

Dev/Release management registers native service definitions before activation: systemd on Linux, launchd on macOS, SCM on Windows. Windows needs its configured service account and registration password. Registration prepares services without starting them. PostgreSQL must already run and NATS must be installed. Standalone release installations manage their own independent service chain.

### Usage and quota

Home → Usage Attribution reports tokens; AI Activity & Accounts reports account quotas. Below the Close threshold, automatic work pauses until quota recovers or an authorized operator changes the threshold.

## 6. Appendix: the components of a Portal deployment

| Component | Responsibility | Source of operational details |
| --- | --- | --- |
| PostgreSQL 16 | Accounts, permissions, Lineages, conversations, executions, and resource indexes in domain schemas | Database service definition, preparation script, operating design. |
| NATS | Change notifications and live refresh | NATS service definitions and configuration. |
| Portal Web | Pages and HTTP, including `/api/instructions/execute` | Web service definition. |
| ResourceService | Resources and permission-related operations | Resource service definition. |
| SocietyService | Lineages and Residents | Society service definition. |
| ExecutionService and ExecutionWorker | Model request scheduling and execution | Execution service and worker definitions. |
| Portal Router | User routing to Dev or Release | Router service and deployment registry. |
| Provider backends | AI tools such as Codex App Server, Claude Code, and Kimi Code | Provider service definitions and operating design. |

Python dependencies are in `requirements*.txt`. Services read the configured `PORTAL_DATABASE_URL`. Dev runs working source for development; Release runs published source for everyday use. They share the configured database and background services. Adapt paths, system accounts, and ports to your installation.

For external access, put a TLS-terminating reverse proxy in front of Portal. Set `PORTAL_PUBLIC_ORIGIN` to the public Portal URL so recovery emails use the correct address.
