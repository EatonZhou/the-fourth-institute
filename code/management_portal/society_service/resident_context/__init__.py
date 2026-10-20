"""Resident context: the summaries and search index Portal keeps for every Resident.

A SocietyService module of the Resident domain.  It mirrors the Resident
catalog, notices conversation changes, queues summary work and keeps a
vector index of the summaries, all mechanically, in a SocietyService
background worker.  The summaries themselves are written by the Resident
holding the Portal duty ``resident-summary``: it claims due work, reads the
evidence and submits each summary through HATG instructions, as itself and
with its own settings.

Storage is the ``resident_context`` PostgreSQL schema.
"""
