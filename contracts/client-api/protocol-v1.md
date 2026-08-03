# Client API protocol v1

## Purpose

Client API v1 is the versioned network boundary between one OpenPPX Node and CLI, Desktop, or a future client. The client can run on the same computer or connect over a trusted LAN.

The project has not published a stable compatibility promise yet. The current v1 development baseline is the contract from which future version guarantees will begin.

## Transport

- JSON endpoints use HTTP below `/api/v1`.
- Long-running Run events use Server-Sent Events (SSE).
- Protected requests use `Authorization: Bearer <token>`.
- Credentials are never accepted in URLs or query strings.
- Action field names use camel case and the common Action envelope.
- Existing Agent, Session, Message, and Run resource projections retain their documented wire fields until they move behind generated domain contracts.

## Handshake

Clients first call public `GET /api/v1/health` for liveness and protocol negotiation. The response identifies:

- `service: openppx-client-api`;
- `product_version`;
- `protocol_version: 1`;
- readiness/state (`healthy` or `needs_configuration`);
- timestamp.

Protocol compatibility is determined by the protocol version, not the product version. An incompatible or malformed response is reported as an error; clients do not synthesize production data.

After protocol negotiation, clients call protected `GET /api/v1/node`. It returns stable Node identity, supported protocol range, caller-visible capabilities, enabled Agent count, and authentication status. Local paths and Secrets are not projected.

An unconfigured loopback Node exposes the minimum setup surface so Desktop and CLI can complete onboarding. It must not report itself as ready before a real `setup.hello` succeeds for the active resource revisions.

## Authentication

- Every operation except the minimal public health projection is protected when a bearer token is configured.
- JSON and SSE use the same bearer header.
- Non-loopback binds are rejected unless authentication is required and a non-empty process token is available.
- Token comparison is constant-time.
- Authentication failures return HTTP 401 with a stable error and never echo the credential.
- Manual loopback development may disable authentication. Desktop-supervised local Nodes use a random per-process token.

## Common Action contract

Action discovery and execution are the shared control surface:

- `GET /api/v1/actions` returns a caller-aware catalog and supports namespace/client-projection filters.
- `POST /api/v1/actions/invoke` executes one registered Action.

Success:

```json
{
  "protocolVersion": 1,
  "requestId": "req_...",
  "correlationId": "corr_...",
  "ok": true,
  "result": {}
}
```

Failure:

```json
{
  "protocolVersion": 1,
  "requestId": "req_...",
  "correlationId": "corr_...",
  "ok": false,
  "error": {
    "code": "permission_denied",
    "message": "The action is not permitted.",
    "details": {}
  }
}
```

The Action Executor enforces availability, strict input, policy scope, permission, confirmation, and audit before calling a domain handler. Errors are stable and redacted.

Major Action namespaces include:

- `system`, `setup`, `config`, `secret`, and `model`;
- `extension`, `plugin`, `app`, and `mcp`;
- `session`, `run`, `task`, `skill`, and `command`;
- `operations`, `cron`, `heartbeat`, `usage`, and `audit`.

## First setup

First-run clients use:

- `setup.status` to obtain provider catalog, recommended workspace, readiness, and current revisions;
- `setup.apply` to validate and atomically write Node, Agent, Model Profile, and optional Secret input;
- `setup.hello` to create a Session and execute a real model turn.

Secret values may enter the protected apply request but never appear in status, apply, Hello, diagnostics, or audit responses.

## Agent and Run operations

Desktop currently uses versioned operations for:

- Node/runtime bootstrap;
- Agent listing;
- Session list/create and message history;
- Run start/cancel;
- ordered Run event streaming.

Run SSE events include `run.started`, `message.created`, `message.delta`, `step.updated`, `message.completed`, `message.failed`, `run.cancelled`, and `run.finished`. Events carry stable IDs/sequences so a client can reconnect with `Last-Event-ID`, suppress duplicates, and continue within the bounded replay window.

An ADK error or empty final response produces failure, not a successful blank message.

## Extension and Operations projection

Extension inventory and lifecycle use Actions rather than a parallel mutable HTTP registry. Inventory responses are client-safe and omit SecretRefs, source credentials, URL secrets, local staging paths, and backend exception text.

Operations Actions expose bounded Node status, component health, Task facts, Cron, Heartbeat, usage, and redacted audit rows. Clients do not query Node SQLite databases directly.

## Compatibility rules after stabilization

Once a stable protocol is declared:

- adding optional fields is compatible;
- removing or renaming fields, changing meaning, or changing event semantics requires a new protocol version;
- canonical schema and fixtures must change with Python and TypeScript consumers;
- clients must reject unsupported required versions rather than guessing.
