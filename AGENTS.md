# Portal development

The repository root contains shared source in `code/` and instance state under
`instance/`. Only `instance/example_instance/seed/` belongs in Git. Never commit
credentials, live databases, provider accounts, private conversations or runtime files.

For a host with local development configured, read
`git rev-parse --path-format=absolute --git-common-dir`, then
`tfi-development.json` in that directory. Make new Portal fixes in its `dev`
worktree. `main` is the deployed/public tree. Preserve other contributors'
uncommitted changes; do not reset, stash or publish them as part of your task.

Use `code/scripts/tfi_dev.py` for the local workflow described in
`code/docs/design/local-development.md`. Only main is pushed. Each test instance
has its own data, database, queues and service names. Never start Dev consumers
against the production instance database. Merging code and activating a running
instance are separate operations; restart only affected application components,
preserving PostgreSQL and provider services unless the task requires otherwise.

Old archived repositories and old conversation paths are historical evidence,
not the current development source.
