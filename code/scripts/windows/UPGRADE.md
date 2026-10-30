Windows application updates
===========================

An application with shared `code` uses `tfi_release_install.py
application-check/application-upgrade/application-recover`. The portable
`upgrade` command still rejects shared source. Run the **new engineering's**
installer outside the installed application, with `--root` set to the whole
application directory. `--manifest` selects the immutable update engineering.
The artifact and its source commit belong to the existing ReleaseStore run and
receipt; `windows/build_update.py` consumes that run ID and the same payload as
the full installer. It creates no alternative release catalog.

`application-check` performs no installation/service mutation. It validates the
full engineering inventory, explicit origin manifests, every registered instance,
service ownership, PostgreSQL 16 data directory/tablespaces, schema origins,
disk space, internal aliases and current queued/active work. Failure to drain is
a prerequisite failure; the updater does not cancel user work. Successful checks
are observations, not a reservation: the mutating command rechecks under locks.

`application-upgrade` must run in the signed-in desktop session for user services.
It locks the desktop launcher and all registered instances, stops owned services,
rechecks work while PostgreSQL remains available, then stops PostgreSQL. A private,
verified copy of the entire application pairs code, configuration, accounts,
workspace and physical databases. Internal NTFS junctions retain their relative
target semantics in each copy. No seed database or example data is imported.

Only the declared v60–v65 Lineage chain is allowed; other domain versions must
match. Retired run-as configuration is archived in `tfi_migration_archive`; the
current application uses Lineage Leader identity. Core Python/native binaries
remain unchanged. The new offline wheelhouse and formal dependency owner verify
or prepare core packages; all Provider installations and account data remain local.

The private sibling `.<application>-maintenance/upgrade.json` is a transaction
journal, not a version registry. After interrupted mutation, ordinary starts are
blocked until `application-recover`. Recovery verifies the stopped pair before
changing data, preserves the failed generation, restores the previous application
and database together, and starts only its own services. Neither a backup nor a
failed generation is deleted automatically. Pre-backup failure restarts the old
services without changing their code or database.

Current scope: native Windows user service installations, internally owned data,
same base Python/native runtimes and PostgreSQL major. All application instances
must pass; unregistered directories, SCM, external storage and unknown schema
origins are refused. Linux portable and macOS application upgrade owners are
unchanged. A built artifact remains a candidate until native upgrade and recovery
evidence for that exact artifact is recorded in its ReleaseStore receipt.
