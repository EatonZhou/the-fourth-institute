Windows program updates
=======================

A newer Windows package updates only the program, following the instance
protocol's "code update". There is no separate upgrade transaction, snapshot or
receipt.

1. Run the new `TheFourthInstitute-Setup-x64.exe` and choose the folder of the
   installed version. Setup first runs the installed launcher with `--stop-all`,
   which stops every registered instance whose deployments run this program.
   If any instance cannot be stopped, the program is not changed.
2. Setup replaces `code/`, `branding/`, the bundled Python runtime and its wheel
   set, and the prepared engineering bundle. Instance roots are outside the
   program folder (by default `%LOCALAPPDATA%\TFI\instances\<name>`) and are not
   touched.
3. The next desktop start finds that the instance's service definitions still
   name the previous code environment and runs
   `tfi_release_install.py update-code --instance <name>`: it prepares the new
   code's dependencies, refreshes the service definitions and restarts the
   services. Services validate their database on startup. An existing schema
   that requires an explicit conversion must be migrated before restarting;
   an empty-database installation check does not establish upgrade compatibility.

The example instance's content is used only when an instance is created; a code
update never imports it again. When a fallback is wanted, back the instance up
with backup software before updating (database dump, areas, `secrets/`,
`instance.json` and `site.json`).

Installed LineageData v65 databases (including 3dc0798) require the offline
conversion in `scripts/migrate_installed_database.py` before v66 code can start.
Keep this converter in packages while those installed releases are supported;
completion on the development server does not complete users' migrations.

After installing the matching program, stop the instance's services and start
only its PostgreSQL component. Select its existing `PORTAL_INSTANCE_ROOT` and
run the converter using the installed code's Python environment, from `code/`:

```text
python -m scripts.migrate_installed_database --report <archive>/migration-preview.json
python -m scripts.migrate_installed_database --apply --report <archive>/migration-applied.json
```

Run the apply command only after the preview succeeds and a current backup has
been verified. The converter checks that PostgreSQL has no other clients. It
converts the registry identity name, directory ownership and Family controls in
one transaction, validates the resulting owner schemas, and compares protected
business-field hashes. Preview executes the conversion then rolls back; normal
PostgreSQL sequence allocation may leave harmless revision gaps. It does not
move or delete files. Conflicts abort the transaction; do not bypass the checks.
Then resume `update-code --instance <name>` and verify services and retained data.

Some older Windows instances still store Article manuscripts in
`data/share/Articles/<article_id>` and project registrations as `/Projects/<id>`.
These are not valid instance area references. Do not add an `articles` area or
disable location validation. The explicit compatibility path is:

```text
python -m scripts.migrate_installed_database --relocate-legacy-articles --report <archive>/migration-preview.json
python -m scripts.migrate_installed_database --prepare-article-copies <archive>/migration-preview.json --report <archive>/article-copies.json
python -m scripts.migrate_installed_database --relocate-legacy-articles --apply --expected-preview <archive>/migration-preview.json --report <archive>/migration-applied.json
```

The preview requires an unambiguous registered project and its existing directory
under the projects area. It records all source files and their hashes, plans
`projects:<project>/articles/<article_id>`, converts project and Article locations
in the rollback transaction, and validates the full database migration. Copy
preparation rechecks that plan, copies without replacing any target, and verifies
every file and empty directory; original manuscripts remain in place. Apply
requires the same source inventories, verified target copies, and protected
business records as the preview, then commits the path and schema conversions
together. Changed or conflicting trees stop the operation. Keep the old files
and migration evidence archived until service and Article access are verified.
