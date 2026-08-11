# Historical Session Access

OpenTeamwork lets an Agent read and search retained conversations without widening ordinary Desktop Session access. The implementation uses the authoritative Google ADK Session service and applies a trusted user-plus-Agent authorization decision before any content is returned.

## Session lifecycle

Sessions have three product-visible lifecycle states:

- `active`: visible in Active and can continue;
- `archived`: visible in Archived and can continue after restoration;
- `removed`: hidden from both lists, cannot continue, but remains available to authorized historical search.

The Desktop `Delete` action moves a Session to `removed`. It does not physically delete the ADK Session or its Session-scoped Artifacts. Physical purge is a separate future administrative lifecycle and is not exposed by the ordinary Session action.

## Authorization matrix

Every decision uses trusted identities from the authenticated ADK invocation and Node Runtime. The model supplies only the target Agent or query; it cannot supply the source user, source Agent, or source privilege.

- Every Agent can read and search its own Sessions.
- Within one user, an Agent can access another Agent whose privilege is lower than or equal to its own.
- Across users, the source user must be `high` or `root`, and the source Agent must be `high` or `root`.
- Cross-user access is limited to non-root target users and non-root target Agents.
- Cross-user access to a root user's history or a root Agent's history is always denied.
- Agents owned by the same root user follow the normal same-user privilege rule.

The effective source Agent privilege is the lower of the current Agent Config level and the trusted permission snapshot pinned to the current Runtime call. This prevents a stale Runtime from gaining access after a privilege change.

## Agent resolution and retrieval

The Agent first resolves the exact display name inside its authorized scope. Display names are mutable and may be duplicated, so an ambiguous result returns a bounded candidate list and requires clarification. The Agent must not guess an immutable Agent ID.

The read-only tool set supports:

- listing retained Sessions in a half-open ISO 8601 time range;
- exact-substring keyword search;
- reading one retained Session;
- opaque cursor pagination with a maximum page size of 50;
- stable Agent, owner, Session, and message citations.

Chinese phrases use exact substring matching. Space-separated terms use explicit `and` or `or` behavior. A request for all matching history must follow cursors until `nextCursor` is empty instead of loading an unbounded transcript in one call.

## Indexed content and trust boundary

The first version searches user and assistant message text plus attachment filename markers. It excludes attachment bodies, tool payloads, thought content, and Artifacts. Historical messages are treated as quoted, untrusted data rather than new instructions to the Agent.

Every cross-Agent list, search, and read decision writes a durable audit record containing the source user and Agent, target scope, decision reason, query shape, and returned citations. Allowed cross-Agent access fails closed if the audit record cannot be persisted. Own-Agent history reads do not create this additional cross-Agent audit record.
