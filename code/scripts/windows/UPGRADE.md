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
disk space, internal aliases and current queued/active work. Proven unsubmitted
waiting work is retained with its original inputs and checkpoints; submitted or
unknown work is refused. The updater does not cancel user work. Successful checks
are observations, not a reservation: the mutating command rechecks under locks.

`application-upgrade` must run in the signed-in desktop session for user services.
It locks the desktop launcher and all registered instances and holds database
write barriers. Before client ownership capture, it journals the original
database admission setting, then temporarily closes new connections using a
separate maintenance database. Existing or late-starting clients must still pass
the ownership checks; unknown clients are never granted cancellation authority.
After stopping owned services, zero other clients and an unchanged retained-work
proof are required. Admission is restored and checked again while write barriers
remain held, before stopping PostgreSQL. A private,
verified backup of the entire application pairs code, configuration, accounts,
workspace and physical databases. Bytes are stored by digest with a checked
path/link inventory, so deep business paths do not become longer in the backup.
Internal NTFS junctions retain their relative target semantics on restoration.
The backup is not a runnable copy. No seed database or example data is imported.

Only the declared v60–v65 Lineage chain is allowed; other domain versions must
match. Retired run-as configuration is archived in `tfi_migration_archive`; the
current application uses Lineage Leader identity. Core Python/native binaries
remain unchanged. The new offline wheelhouse and formal dependency owner verify
or prepare core packages; all Provider installations and account data remain local.
Stopped Codex account definitions are included in the backup, withdrawn while
the dependency interpreter changes, then registered against the verified new
interpreter. Recovery restores their original definitions and restart set.

The private sibling `.<application>-maintenance/upgrade.json` is a transaction
journal, not a version registry. After interrupted mutation, ordinary starts are
blocked until `application-recover`, also run from the signed-in desktop.
Recovery verifies the stopped pair before
changing data, preserves the failed generation, restores the previous application
and database together, and starts only its own services. Neither a backup nor a
failed generation is deleted automatically. Pre-backup failure restarts the old
services without changing their code or business rows. If interrupted while
database admission is closed, recovery first validates the recorded cluster and
database identities and restores admission through the maintenance database.
Failure to restore admission leaves the journal recoverable and does not start
business services. Client refusals include private, anonymous predicate evidence;
an unavailable observation never counts as proof that a process has exited.

Current scope: native Windows user service installations, internally owned data,
same base Python/native runtimes and PostgreSQL major. All application instances
must pass; unregistered directories, SCM, external storage and unknown schema
origins are refused. Admission control requires an accessible `postgres` or
`template1` maintenance database distinct from every target, with catalog-lock
and database-owner privileges. Originally closed admission and multiple targets
in one PostgreSQL cluster are refused. Linux portable and macOS application upgrade owners are
unchanged. A built artifact remains a candidate until native upgrade and recovery
evidence for that exact artifact is recorded in its ReleaseStore receipt.
