# OpenTeamwork

**An open-source Agent OS for secure, persistent, real-world AI work.**

Run and manage AI agents from one Node, with durable sessions, skills, automation, extensions, permissions, and audit built on Google ADK.

If OpenTeamwork is useful to you, give it a ⭐ so more builders can discover it.

> Developer preview — not yet production-ready.

## News

- **2026-08-09 — v0.6.0 Developer Preview:** teach Agents with `/make-skill`, automate recurring work, and govern real execution with isolated workspaces, permissions, Apps, MCP, and audit.
- **2026-08-06 — v0.5.4:** added durable Goals, TaskFlows, automation, typed commands, diagnostics, and document/image Artifacts.
- **2026-08-05 — v0.5.3:** introduced the unified Plugin/App/MCP/Skill extension platform and full Desktop management.
- **2026-08-04 — Unified Node architecture:** brought Config, Actions, Extensions, Operations, audit, onboarding, and LAN access under one governed contract.
- **2026-08-02 — Desktop v0.5.1:** launched the three-column workspace, local/LAN connections, Run streaming, and reusable TypeScript client.

## Architecture

```text
CLI          OpenTeamwork Desktop       Future clients
 |                  |                       |
 +------------------+-----------------------+
                    |
           Shared Client Contract
             HTTP + SSE + Actions
                    |
              OpenTeamwork Node
  Config / Models / Extensions / Operations / Audit
                    |
       Runtime Supervisor + immutable snapshots
                    |
              Google ADK runtime
```

The Node is the source of truth. Clients may keep device-local preferences such as window layout, but they do not read or rewrite Node business files.

## What is implemented

- Strict Node and Agent Config resources with validation, revision conflicts, preview/apply, and redacted diagnostics.
- Model Profiles with explicit provider, model, capabilities, credential reference, workload role, and fallback policy.
- A governed Extension Platform for Product Plugins, Apps, direct MCP servers, and Skills.
- Builtin, local-directory, local-archive, fixed-Git, and injected-catalog extension sources with staging, digest validation, risk checks, and immutable installed content.
- A typed Action Registry shared by CLI, Desktop, slash commands, and future clients.
- Persistent sessions, artifacts, memory, TaskRuns, checkpoints, supervised long tasks, and workflow facts.
- Node-owned Task scheduling, Cron, Heartbeat, usage, health, and redacted Action audit facts.
- A thin TypeScript client and an Electron/React Desktop workspace that can save and switch between a local Node and multiple trusted-LAN Nodes without exposing their bearer tokens to the Renderer.
- Desktop lifecycle management for Extensions, Operations, Agents, and Sessions, plus Session-scoped document, spreadsheet, PDF, presentation, text/code, and image upload/download with durable Artifact references.
- Node-authoritative attachment policy with extension, MIME, magic-byte, archive, XML, page, cell, character, image-pixel, per-file, per-message, and Session-ownership limits. Original bytes remain downloadable while bounded deterministic projections are supplied to the model.
- Google ADK-native Agent, Runner, Session, Artifact, Memory, MCP, confirmation, rewind, compaction, and evaluation integration.

## Requirements

- Python 3.14
- Node.js and pnpm for Desktop development
- A supported model provider account or local model endpoint
- macOS ARM64 for the currently supported packaged Desktop preview. Source development on other systems is not a release-support commitment.

## Install from source

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For an offline checkout whose build dependencies are already installed, use:

```bash
python -m pip install --no-build-isolation -e .
```

## First setup

`otw setup` configures a Node, one Agent, one Model Profile, protected credentials, and a real first model turn. By default it uses `~/.openteamwork` as the Node root and asks for an API key with a hidden prompt when the provider requires one.

```bash
otw setup \
  --provider google \
  --model <provider-model-id> \
  --agent-id main \
  --workspace <workspace-directory>
```

Use `--no-hello` only when configuration must be saved before the model is reachable. A configured Node is not reported as ready until `setup.hello` succeeds for the exact Config revisions.

Start the Node:

```bash
otw node run
```

The default local endpoint is `http://127.0.0.1:18765`.

## Desktop

Install workspace dependencies and start the application from the repository root:

```bash
pnpm install
pnpm desktop:dev
```

OpenTeamwork Desktop can supervise a local Node or connect to an already-running Node. The packaged Desktop remains a thin client; Python, model credentials, Agent data, and Node databases are installed and operated separately.

See [apps/desktop/README.md](./apps/desktop/README.md) for development, packaging, and LAN instructions.

## Trusted LAN operation

On the machine that runs the agents, configure a non-loopback listener and required authentication during setup:

```bash
otw setup \
  --listen-host 0.0.0.0 \
  --listen-port 18765 \
  --authentication required \
  --provider google \
  --model <provider-model-id>
```

Then start the Node with a strong bearer token:

```bash
export OPENTEAMWORK_CLIENT_API_TOKEN='<random-secret>'
otw node run
```

Connect Desktop or CLI to `http://<node-lan-address>:18765` with the same token. A non-loopback bind without a token is rejected. This mode is intended only for a trusted LAN; do not expose the HTTP endpoint directly to the public internet.

## CLI

`otw` is the documented terminal command. The installed `openteamwork` command is an equivalent long-form entry point.

The stable top-level groups are deliberately small:

```text
otw status
otw setup
otw node run|service
otw action list|invoke
otw command
otw config read|validate|preview|apply
otw model list|read|readiness|select|apply
otw extension list|get|readiness|preview|install|enable|disable|remove
otw operations status|health|tasks|cron|heartbeat|usage|audit
```

Commands that manage a running Node accept `--url` and `--token`. Add `--json` for machine-readable output. Use `otw <group> --help` for exact inputs and optimistic-revision requirements.

Action discovery and direct invocation are useful for debugging shared client behavior:

```bash
otw action list --projection cli
otw action invoke system.status --input-json '{}'
```

Slash commands use the same Action catalog:

```bash
otw command '/status'
otw command '/skills' --agent main
otw command '/history' --agent main --session <session-id>
```

## Extensions

OpenTeamwork uses four product-level extension types:

- **Plugin:** a versioned declarative bundle that can provide Skills, App definitions, MCP templates, Agent templates, schemas, and documentation.
- **App:** a managed external-service integration with product identity, authorization state, grants, and tool policy.
- **MCP:** a directly managed local or remote Model Context Protocol server.
- **Skill:** instructions, references, and controlled scripts loaded for an Agent.

Every install follows a staged lifecycle:

```text
discover -> stage -> validate -> preview -> confirm -> install -> enable -> test
```

Preview first, retain the returned digest, then install with that exact digest. Installed content is immutable and an active Run keeps its pinned extension snapshot.

```bash
otw extension list --agent main
otw extension preview skill local_directory <source-directory>
otw extension install skill local_directory <source-directory> <expected-digest>
```

## Operations

```bash
otw status
otw operations status
otw operations health
otw operations tasks --limit 20
otw operations cron list
otw operations heartbeat status
otw operations usage --limit 20
otw operations audit --limit 50
```

`otw status` is a shortcut for `otw operations status`.

Cron and Heartbeat are owned by the long-lived Node process. Their actions, failures, and TaskRuns appear in the same Operations and audit surfaces used by Desktop.

## Security model

- Secrets are represented by `SecretRef`; ordinary resource JSON, diagnostics, audit, and client responses never contain secret values.
- Non-loopback Client API access requires bearer authentication.
- High-risk Actions require both policy permission and explicit confirmation.
- Action audits store bounded identities, decisions, and outcomes rather than request or response payloads.
- Extensions are staged and validated before activation; declarative Product Plugins cannot execute arbitrary host initialization code.
- Docker sandbox support is available for dangerous local execution, but access to the Docker daemon remains host-powerful.

See [docs/PERMISSIONS.md](./docs/PERMISSIONS.md), [docs/MCP_SECURITY.md](./docs/MCP_SECURITY.md), and [docs/SANDBOX.md](./docs/SANDBOX.md).

## Repository layout

```text
openppx/                    Python Node, domains, runtime, and built-in Skills
packages/client/            Shared TypeScript Client Contract implementation
apps/desktop/               Electron/React Desktop
contracts/client-api/       Versioned schemas, protocol notes, and fixtures
tests/                      Unit, integration, contract, architecture, and eval tests
docs/                       User and operator documentation
```

## Development verification

Run the canonical offline-friendly gate from the repository root. It uses package-local tools and does not require Corepack to download pnpm:

```bash
./.venv/bin/python scripts/verify.py
```

Before creating a macOS ARM64 preview artifact, include the unsigned directory package and packaged-preload check:

```bash
./.venv/bin/python scripts/verify.py --package
```

Use `--list`, `--skip-python`, or `--skip-build` only for diagnostics; the full gate remains the acceptance source of truth.

## Documentation

- [Project architecture](./docs/PROJECT_OVERVIEW.md)
- [Configuration and models](./docs/CONFIGURATION.md)
- [Static execution permissions](./docs/PERMISSIONS.md)
- [Operations](./docs/OPERATIONS.md)
- [Sessions, attachments, and artifacts](./docs/ARTIFACTS.md)
- [Client API contract](./contracts/client-api/README.md)
- [Desktop](./apps/desktop/README.md)
- [MCP and extension security](./docs/MCP_SECURITY.md)
- [Office connectors](./docs/OFFICE_CONNECTORS.md)
- [Sandbox](./docs/SANDBOX.md)
- [Use cases](./docs/USE_CASES.md)

## Current boundaries

- CLI and Desktop are first-class clients; a mobile client is a future consumer of the same contract, not part of the current build.
- Multiple trusted-LAN targets and encrypted endpoint-bound tokens are implemented. Automatic TLS, discovery, identity pairing, token rotation/revocation, SSH/Tailnet setup, and a public relay are future work.
- Message attachments currently support modern DOCX, XLSX, CSV, text-based PDF, PPTX, PNG, JPEG, WebP, and a bounded set of UTF-8 text/code formats. Legacy `.doc`, `.xls`, and `.ppt`, scanned-PDF OCR, encrypted PDFs, and arbitrary binary files are deliberately rejected with a conversion or capability message.
- Public extension catalogs, cloud hosting, improved memory, self-evolution, and deeper long-task intelligence remain later product layers over the current Node foundation.
- The current macOS Desktop artifact is a developer preview and is not signed or notarized.
