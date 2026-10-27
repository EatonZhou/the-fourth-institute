# Portal development

This file is the repository's development entry. A hosted Resident's identity is
the lowercase `resident.md` in its Resident Folder, explicitly injected by Portal.
Portal's Codex runtime disables automatic project-document loading; do not rely on
this `AGENTS.md` to deliver rules. Shared rules come from `tfi-agent-rule/SKILL.md`
and the role file selected by Portal. `/guide/agent.md` is a separate guide for
external clients acting as Users, not a Resident identity or system prompt.

Read the current Design indexed by `code/SPEC.md`: **Portal 共享运行基础与 Dev 到
Release 的快速发布流程** (`decision_08bb2d9141f9`). The development rules in
`code/core_skills/tfi-agent-rule/portal-development.md` explain the same workflow.

Read `tfi-development.json` in `git rev-parse --path-format=absolute --git-common-dir`.
All Residents edit its one shared **dev worktree**. Its **main worktree is Release**.
Both directories exist simultaneously; switching the Web entry does not checkout
a branch. Do not create task branches or worktrees. Only Release pushes `main`.

After a feature/fix and relevant necessary checks (except changes that do not
require a service restart, such as static frontend changes), run `code/scripts/tfi_dev.py
publish --message "..." --path code/path` (repeat --path; --all explicitly includes
all work). Publication is part of the authorized task. Do not wait for a clean Dev
tree, other agents to finish, a full regression, or another approval. Selected files
are committed as a whole: other agents' changes in those files, staged work and
already committed ancestors may accompany the release. This is intentional. Keep
later concurrent edits; never reset/stash them or reconstruct a pure personal diff.
The exception skips automatic publication for each change, not the necessary checks.
Determine restart needs from how the change takes effect, not from the publisher's
current automatic restart behavior.

Dev and Release Web share the installed database, accounts, sessions and backend
services. Shared packages run from Dev; Web code runs from its own worktree. Use
`code/scripts/tfi_development_runtime.py` to configure/activate this pair; do not
create a second database or duplicate queue consumers to implement a version switch.
Activation restarts affected application components and hands off Workers. Preserve
PostgreSQL, NATS and provider account services during ordinary code publication.

Only source and `instance/example_instance/seed/` belong in Git. Never commit live
instance data, credentials, provider accounts, private conversations or runtime files.
Archived repositories and historical conversation paths are not development sources.
