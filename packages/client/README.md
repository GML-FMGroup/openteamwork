# @openppx/client

Private workspace package implementing the transport-neutral TypeScript client for the OpenPPX Client API.

## Responsibilities

- Protocol version, health, Node identity, capability, and authentication projections.
- Fetch-based JSON transport and structured request errors.
- SSE parsing helpers used by Desktop Run streaming.
- Typed Agent, Session, Message, and Run models.
- Common Action catalog/invocation envelopes and clients.
- System, Model, Setup, Command, Extension, and Operations facades.
- Canonical fixture validation shared with the Python contract exporter.

## Boundary

This package talks to one already-selected Node endpoint. It does not:

- persist credentials;
- start a local Node;
- expose Electron IPC or native APIs;
- own React state or UI projections;
- read Node files or databases;
- implement domain validation or mutation policy.

Desktop keeps long-lived LAN credentials in Electron Main and calls this package there. The sandboxed Renderer communicates only through typed preload IPC.

## Development

```bash
cd packages/client
./node_modules/.bin/vitest run
./node_modules/.bin/tsc --noEmit
```

Any client-visible contract change must update `contracts/client-api/v1`, Python contract tests, this package's fixtures/tests, and Desktop consumers together.

Public npm publication is not part of the current developer preview.
