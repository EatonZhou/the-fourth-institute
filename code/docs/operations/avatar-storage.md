# Avatar persistence

Portal stores User, Resident, Lineage and Project avatars in the instance's
PostgreSQL database, in `portal_avatars.avatars` (prefixed by
`PORTAL_DATABASE_NAMESPACE` when configured). The subject binding, sprite
selection or normalized PNG bytes, and content version are committed together.
An uploaded avatar remains a PNG of at most 256 pixels. Image URLs carry the
content hash.

This is instance data. Include the schema in full database backups and restores;
do not export it wholesale as public example data. PostgreSQL physical backups
and unfiltered `pg_dump` include the bytes. Restricted domain exports need their
own explicit selection of avatar subjects.

Nothing is read from files: the former `~/.tfi/portal/{user,resident,scope}_avatars`
directories were only an import source for subjects without a database row, and
that import code has been removed. Deleting an avatar, or moving it when a Project is
renamed, leaves a small tombstone row in the same transaction; readers treat it as
no avatar.
