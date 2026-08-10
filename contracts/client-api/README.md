# OpenTeamwork Client API contract

This directory is the versioned boundary between the shared Client API runtime and OpenTeamwork clients, including OpenTeamwork Desktop.

- `protocol-v1.md` defines transport, authentication, Action, setup, Run-event, and future compatibility rules.
- `fixtures/health-v1.json` is a canonical compatible health handshake.
- `fixtures/health-incompatible.json` represents a well-formed but unsupported future protocol.
- `fixtures/node-v1.json` is authenticated Node identity and capability metadata.
- `v1/schema.json` is the generated common Action and Extension contract.
- `v1/fixtures/extension-*.json` and `v1/fixtures/envelope-extension-list.json` are canonical Extension projections consumed by Python and TypeScript tests.
- `v1/fixtures/action-invoke-setup-*.json` and `v1/fixtures/envelope-setup-*.json` define first-run configuration and verified-Hello exchanges.

The fixtures are consumed by both Python and TypeScript tests. Contract changes must update the documentation, fixtures, and both test suites together.

The current development baseline intentionally has no compatibility adapter for earlier pre-release implementations. Compatibility commitments begin after the first stable protocol is declared.
