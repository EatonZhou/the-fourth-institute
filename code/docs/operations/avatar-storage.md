# Avatar persistence and migration

Portal stores User, Resident, Lineage and Project avatars in the instance's
PostgreSQL database, in `portal_avatars.avatars` (prefixed by
`PORTAL_DATABASE_NAMESPACE` when configured). The subject binding, sprite
selection or normalized PNG bytes, and content version are committed together.
An uploaded avatar remains a PNG of at most 256 pixels. Existing HTTP endpoints,
authorization and content-hash image URLs are unchanged.

This is instance data. Include the schema in full database backups and restores;
do not export it wholesale as public example data. PostgreSQL physical backups
and unfiltered `pg_dump` include the bytes. Restricted domain exports need their
own explicit selection of avatar subjects.

Legacy files under the instance's `HOME/.tfi/portal/{user,resident,scope}_avatars`
(or `PORTAL_*_AVATAR_ROOT` overrides) are import sources. The runtime imports a
subject on first access if it has no database row. It validates uploaded image
hashes before importing. All subsequent writes use the database. Deletion leaves
a small tombstone so an old file cannot bring the avatar back. Project rename
moves its avatar and marks the old identity in one transaction.

For a controlled upgrade, pause avatar-writing consumers, then run these commands
using that instance's Python and environment, including `HOME`,
`PORTAL_DATABASE_URL` and its optional namespace:

```sh
python code/scripts/migrate_avatar_database.py
python code/scripts/migrate_avatar_database.py --apply
```

Run from the repository root. The first command validates without writing;
the second imports and reads back every record and image. Repeated imports
preserve existing database choices, including deletions. Resolve reported errors
before declaring migration complete, then activate the new code in all avatar
consumers. Keep legacy files privately until database backup/restore and HTTP
image checks pass. Do not roll back to file-writing code after users change
avatars without explicitly migrating those newer database changes.
