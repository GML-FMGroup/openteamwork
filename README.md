# OpenPPX

OpenPPX is an open platform for running and managing personal AI agents. One OpenPPX Node owns the durable state, policies, extensions, automation, and Google ADK runtimes; CLI, Desktop, and future clients consume the same versioned contract.

The current source tree is a developer preview. It is not yet a stable or production-ready release.

## News

- **2026-08-04 — v0.5.2 Developer Preview:** published the unified Node architecture as a prerelease, including governed Config and Actions, Plugin/App/MCP/Skill extensions, Operations, OpenAI Codex authentication, Agent and Model Profile management, and a more polished Desktop workspace. The release remains intended for developer evaluation rather than production use.
- **2026-08-04 — Node architecture baseline:** the development branch now uses one Node, Action, Config, Extension, Operations, and Client Contract architecture. First setup, model profiles, Plugin/App/MCP/Skill management, slash commands, audit, usage, Cron, Heartbeat, Desktop onboarding, and LAN access share the same backend facts. The previous parallel runtime and configuration paths have been removed rather than retained as compatibility layers.
- **2026-08-02 — Desktop v0.5.1 Developer Preview:** published a prerelease with the three-column workspace, local/LAN Node connection, resizable panels, improved Run streaming, and the reusable TypeScript client.

## Architecture

```text
CLI          OpenPPX Desktop          Future clients
 |                  |                       |
 +------------------+-----------------------+
                    |
           Shared Client Contract
             HTTP + SSE + Actions
                    |
              OpenPPX Node
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
- A thin TypeScript client and an Electron/React Desktop workspace that can connect to a Node on the same computer or a trusted LAN machine.
- Google ADK-native Agent, Runner, Session, Artifact, Memory, MCP, confirmation, rewind, compaction, and evaluation integration.

## Requirements

- Python 3.14
- Node.js and pnpm for Desktop development
- A supported model provider account or local model endpoint
- macOS or Linux for the current user-service installer

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

`ppx setup` configures a Node, one Agent, one Model Profile, protected credentials, and a real first model turn. By default it uses `~/.openppx` as the Node root and asks for an API key with a hidden prompt when the provider requires one.

```bash
ppx setup \
  --provider google \
  --model <provider-model-id> \
  --agent-id main \
  --workspace <workspace-directory>
```

Use `--no-hello` only when configuration must be saved before the model is reachable. A configured Node is not reported as ready until `setup.hello` succeeds for the exact Config revisions.

Start the Node:

```bash
ppx node run
```

The default local endpoint is `http://127.0.0.1:18765`.

## Desktop

Install workspace dependencies and start the application from the repository root:

```bash
pnpm install
pnpm desktop:dev
```

OpenPPX Desktop can supervise a local Node or connect to an already-running Node. The packaged Desktop remains a thin client; Python, model credentials, Agent data, and Node databases are installed and operated separately.

See [apps/desktop/README.md](./apps/desktop/README.md) for development, packaging, and LAN instructions.

## Trusted LAN operation

On the machine that runs the agents, configure a non-loopback listener and required authentication during setup:

```bash
ppx setup \
  --listen-host 0.0.0.0 \
  --listen-port 18765 \
  --authentication required \
  --provider google \
  --model <provider-model-id>
```

Then start the Node with a strong bearer token:

```bash
export OPENPPX_CLIENT_API_TOKEN='<random-secret>'
ppx node run
```

Connect Desktop or CLI to `http://<node-lan-address>:18765` with the same token. A non-loopback bind without a token is rejected. This mode is intended only for a trusted LAN; do not expose the HTTP endpoint directly to the public internet.

## CLI

The stable top-level groups are deliberately small:

```text
ppx setup
ppx node run|service
ppx action list|invoke
ppx command
ppx config read|validate|preview|apply
ppx model list|read|readiness|select|apply
ppx extension list|get|readiness|preview|install|enable|disable|remove
ppx operations status|health|tasks|cron|heartbeat|usage|audit
```

Commands that manage a running Node accept `--url` and `--token`. Add `--json` for machine-readable output. Use `ppx <group> --help` for exact inputs and optimistic-revision requirements.

Action discovery and direct invocation are useful for debugging shared client behavior:

```bash
ppx action list --projection cli
ppx action invoke system.status --input-json '{}'
```

Slash commands use the same Action catalog:

```bash
ppx command '/status'
ppx command '/skills' --agent main
ppx command '/history' --agent main --session <session-id>
```

## Extensions

OpenPPX uses four product-level extension types:

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
ppx extension list --agent main
ppx extension preview skill local_directory <source-directory>
ppx extension install skill local_directory <source-directory> <expected-digest>
```

## Operations

```bash
ppx operations status
ppx operations health
ppx operations tasks --limit 20
ppx operations cron list
ppx operations heartbeat status
ppx operations usage --limit 20
ppx operations audit --limit 50
```

Cron and Heartbeat are owned by the long-lived Node process. Their actions, failures, and TaskRuns appear in the same Operations and audit surfaces used by Desktop.

## Security model

- Secrets are represented by `SecretRef`; ordinary resource JSON, diagnostics, audit, and client responses never contain secret values.
- Non-loopback Client API access requires bearer authentication.
- High-risk Actions require both policy permission and explicit confirmation.
- Action audits store bounded identities, decisions, and outcomes rather than request or response payloads.
- Extensions are staged and validated before activation; declarative Product Plugins cannot execute arbitrary host initialization code.
- Docker sandbox support is available for dangerous local execution, but access to the Docker daemon remains host-powerful.

See [docs/MCP_SECURITY.md](./docs/MCP_SECURITY.md) and [docs/SANDBOX.md](./docs/SANDBOX.md).

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

```bash
source .venv/bin/activate
python -m pytest -q

cd packages/client
./node_modules/.bin/vitest run
./node_modules/.bin/tsc --noEmit

cd ../../apps/desktop
./node_modules/.bin/vitest run
node --test scripts/verify-preload.node-test.mjs
npm run build
```

## Documentation

- [Project architecture](./docs/PROJECT_OVERVIEW.md)
- [Configuration and models](./docs/CONFIGURATION.md)
- [Operations](./docs/OPERATIONS.md)
- [Client API contract](./contracts/client-api/README.md)
- [Desktop](./apps/desktop/README.md)
- [MCP and extension security](./docs/MCP_SECURITY.md)
- [Sandbox](./docs/SANDBOX.md)
- [Use cases](./docs/USE_CASES.md)

## Current boundaries

- CLI and Desktop are first-class clients; a mobile client is a future consumer of the same contract, not part of the current build.
- Trusted-LAN connectivity is implemented. Automatic TLS, discovery, pairing, SSH/Tailnet setup, and a public relay are future work.
- Public extension catalogs, cloud hosting, improved memory, self-evolution, and deeper long-task intelligence remain later product layers over the current Node foundation.
- The current macOS Desktop artifact is a developer preview and is not signed or notarized.
