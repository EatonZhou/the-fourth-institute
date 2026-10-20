"""HATG ISA: named instructions shared by every Portal service.

The package holds the shared instruction kernel (declarations, validation,
execution records and routing).  Each owning service binds handlers for the
instructions it owns; entries (web, CLI, Harness tool, Lineage runtime) only
identify the caller and forward calls.
"""
