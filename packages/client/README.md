# @openppx/client

Private workspace package containing transport-neutral TypeScript primitives for the OpenPPX Client API.

## Current responsibilities

- Public Agent, Session, Message, and Run event models.
- Client API protocol version and compatibility parsing.
- Bearer authorization header construction.
- Wire-format projection for shared Client API models.
- A Fetch-based JSON/SSE-capable HTTP transport with structured request errors.

## Security boundary

This package accepts credentials but does not persist them. Desktop keeps long-lived credentials in the Electron main process and uses this package there; the sandboxed renderer continues to communicate through the preload bridge.

## Out of scope for this slice

- Electron IPC and native APIs.
- Local OpenPPX Node process management.
- React state and UI projections.
- Browser login/session-token policy.
- Public npm publication.
