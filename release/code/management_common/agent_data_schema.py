"""The AgentData schema marker, apart from its store.

The deployment router reports the marker without loading the execution
package: a router loads only what routing needs, and every source it loads
restarts it on a publication.
"""

AGENT_DATA_SCHEMA = "tfi.agent-data.v18"
