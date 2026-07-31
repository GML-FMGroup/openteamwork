# OpenPPX Client API contract

This directory is the versioned boundary between OpenPPX Node and its clients, including OpenPPX Desktop.

- `protocol-v1.md` defines the compatibility rules and the subset of endpoints used by Desktop.
- `fixtures/health-v1.json` is a canonical compatible health handshake.
- `fixtures/health-incompatible.json` represents a well-formed but unsupported future protocol.

The fixtures are consumed by both Python and TypeScript tests. Contract changes must update the documentation, fixtures, and both test suites together.
