# Client API protocol v1

## Purpose

Client API v1 is the network boundary between a client and one OpenPPX Node. The client may run on the same machine or connect to a Node on another machine on a trusted LAN.

## Transport

- JSON endpoints use HTTP under `/api/v1`.
- Long-running run events use Server-Sent Events (SSE).
- Successful JSON responses use `{ "ok": true, "data": { ... } }`.
- Failed JSON responses use `{ "ok": false, "error": { "code", "message", "details" } }`.
- JSON field names use `snake_case` on the wire.
- Protected requests use `Authorization: Bearer <token>`; tokens are never accepted in query strings.

## Version handshake

Clients first call the public `GET /api/v1/health` endpoint for liveness and protocol negotiation. A compatible response contains:

| Field | v1 requirement |
|---|---|
| `service` | `openppx-client-api` |
| `product_version` | Non-empty OpenPPX release version, or `unknown` in an unpackaged source tree |
| `protocol_version` | Integer `1` |
| `ready` | `true` |
| `state` | `healthy` |

The Desktop client supports protocol version `1` exactly. A different or missing version is not considered healthy. It must be reported as an incompatibility instead of silently using mock data or a legacy transport.

`product_version` identifies the Node implementation and does not determine protocol compatibility.

The public health projection omits local paths, agent counts, Node identity, and capabilities. A client must then call the protected `GET /api/v1/node` endpoint before treating the connection as healthy. That response provides:

- stable `node_id` and `display_name`;
- supported protocol range;
- capability names;
- enabled agent count;
- whether bearer authentication is configured.

## Authentication

- All `/api/v1/*` operations except the minimal public health projection are protected when a token is configured.
- SSE uses the same bearer header as JSON requests.
- Non-loopback binds are rejected at startup unless `OPENPPX_CLIENT_API_TOKEN` is non-empty.
- Token comparison is constant-time.
- Authentication failures return HTTP 401 with error code `UNAUTHORIZED` and never echo the credential.
- Loopback may run without authentication for manual development. Desktop-managed local Nodes still use a random per-process token.

## Desktop endpoints

The current Desktop client uses these v1 operations:

- `GET /api/v1/health`
- `GET /api/v1/node`
- `GET /api/v1/runtime/status`
- `GET /api/v1/agents`
- `GET|POST /api/v1/agents/{agent_id}/sessions`
- `GET /api/v1/sessions/{session_id}/messages`
- `POST /api/v1/agents/{agent_id}/sessions/{session_id}/runs`
- `GET /api/v1/runs/{run_id}/events`

The run stream emits named SSE events such as `run.started`, `message.created`, `message.delta`, `step.updated`, `message.completed`, `message.failed`, `run.cancelled`, and `run.finished`.

## Compatibility policy

- Adding optional response fields is backward compatible.
- Removing or renaming fields, changing field meaning, or changing event semantics requires a new protocol version.
- Legacy bridge and mock modes are explicit development options only; they are not protocol negotiation mechanisms.
