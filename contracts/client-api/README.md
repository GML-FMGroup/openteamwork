# OpenPPX Client API contract

This directory is the versioned boundary between OpenPPX Node and its clients, including OpenPPX Desktop.

- `protocol-v1.md` defines the compatibility rules and the subset of endpoints used by Desktop.
- `fixtures/health-v1.json` is a canonical compatible health handshake.
- `fixtures/health-incompatible.json` represents a well-formed but unsupported future protocol.
- `fixtures/node-v1.json` is authenticated Node identity and capability metadata.
- `v1/schema.json` is the generated common Action and Extension contract.
- `v1/fixtures/extension-*.json` and `v1/fixtures/envelope-extension-list.json` are canonical Extension projections consumed by Python and TypeScript tests.
- `v1/fixtures/action-invoke-setup-*.json` and `v1/fixtures/envelope-setup-*.json` define first-run configuration and verified-Hello exchanges.

The fixtures are consumed by both Python and TypeScript tests. Contract changes must update the documentation, fixtures, and both test suites together.
