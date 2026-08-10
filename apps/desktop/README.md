# OpenTeamwork Desktop

OpenTeamwork Desktop is the Electron/React client for OpenTeamwork. It manages one or more Agents on a local Node or connects to a Node running on another machine in a trusted LAN.

The current application is a developer preview. It is a thin client: packaged Desktop artifacts do not contain the Python backend, model credentials, Node Config, or user databases.

## Architecture

```text
React Renderer
  -> typed preload IPC
  -> Electron Main
  -> @openppx/client
  -> OpenTeamwork Client API (HTTP, SSE, Actions)
  -> OpenTeamwork Node
  -> Google ADK runtimes
```

The Renderer never reads Node business files. Electron Main owns local process supervision, encrypted LAN credentials, protocol validation, and all Client API calls.

If the Client API is unavailable, unauthorized, or incompatible, Desktop reports a recoverable connection error. Production code does not manufacture Agent, Session, Message, Extension, or Operations data.

## Product surfaces

- First-run setup and verified first Hello
- Three-column workspace with Agent, Session, conversation, Progress, and Artifacts
- Action-backed slash command palette
- Models, Extensions, Operations, Agent, and connection settings
- Plugin/App/MCP/Skill creation or installation, editing, readiness tests, Agent enablement, updates, authorization, and safe removal where each resource type permits it
- Task controls, Cron CRUD and run-now, Heartbeat controls, usage, health, and audit projections
- Agent create/edit/enable/archive and Session create/rename/fork/export/archive/delete lifecycles
- Drag, paste, or select modern Office, PDF, UTF-8 text/code, and image attachments; preview or download durable Session Artifacts
- Multiple saved local/LAN Node targets with an explicit active target
- Run streaming, bounded reconnect, replay, cancellation, and connection recovery
- Resizable side panels with device-local layout persistence

## Prerequisites

- Node.js
- pnpm
- Python 3.14
- OpenTeamwork installed in the repository virtual environment

Install JavaScript dependencies from the repository root:

```bash
pnpm install
```

If pnpm reports blocked Electron or esbuild install scripts:

```bash
pnpm approve-builds
pnpm install
```

## Development

From the repository root:

```bash
pnpm desktop:dev
```

The local adapter looks for the source checkout through `OPENTEAMWORK_ROOT`, then uses `<root>/.venv/bin/python` when present. The Node root defaults to `~/.openteamwork` and can be overridden with `OPENTEAMWORK_NODE_ROOT`.

For deterministic debugging, start the backend yourself first:

```bash
source .venv/bin/activate
otw node run --node-root ~/.openteamwork
```

The canonical local endpoint is `http://127.0.0.1:18765`. Desktop can also start a managed local Node when local mode is selected and no compatible process is already reachable.

Useful commands:

```bash
pnpm desktop:test
pnpm desktop:typecheck
pnpm desktop:build
```

If workspace-level pnpm bootstrapping is unavailable but dependencies already exist, run package-local tools:

```bash
cd apps/desktop
./node_modules/.bin/vitest run
./node_modules/.bin/tsc --noEmit
node --test scripts/verify-preload.node-test.mjs
npm run build
```

The repository-level acceptance gate runs those checks consistently and can also package macOS ARM64:

```bash
./.venv/bin/python scripts/verify.py
./.venv/bin/python scripts/verify.py --package
```

## Local Node

Complete setup once:

```bash
source .venv/bin/activate
otw setup \
  --provider google \
  --model <provider-model-id> \
  --workspace <workspace-directory>
```

Desktop onboarding uses the same `setup.status`, `setup.apply`, and `setup.hello` Actions. It opens the workspace only after the current Node, Agent, and Model Profile revisions complete a real model turn.

The local Electron process creates a random per-process bearer token when it supervises a Node. That token remains in Electron Main and is not returned through Renderer diagnostics.

## Remote Node users

The Node administrator provisions product accounts locally:

```bash
otw user add admin@example.com --privilege root
otw user add jiang@example.com --privilege high
```

Remote Desktop login uses an administrator-provided HTTPS origin. The Python Node must remain on loopback behind a reverse proxy on the same host, with deployment authentication enabled. See [Users and Remote App Access](../../docs/USERS.md) for the exact Node, proxy, account, and backup procedure.

In Desktop, choose **Remote Node** and enter the HTTPS origin, account email, and account secret. The secret is not saved. Electron Main encrypts the returned opaque session token through `safeStorage`; ordinary connection JSON contains only public account metadata and a credential reference bound to the exact endpoint and user.

Each saved LAN target has its own encrypted, endpoint-bound credential. The General settings page can switch among saved targets and remove an inactive target. Switching performs a real connection test before changing the active Client API adapter.

Remote connection rules:

- Remote login requires HTTPS; plaintext remote login is rejected before the secret body is read.
- HTTP requests and SSE use the same opaque App session token after login.
- A LAN target is never started or stopped by the Desktop machine.
- Local mode accepts only loopback hostnames; another machine must use LAN mode.
- URLs may contain only scheme, host, and optional HTTPS port—no credentials, path, query, or fragment.
- Do not expose the Python Client API port directly to a LAN or the public internet.

Automatic TLS provisioning, discovery, SSO, password reset, SSH/Tailnet configuration, and a public relay are future work.

## Attachments and Artifacts

The Composer accepts drag-and-drop, clipboard paste, and file selection. One message can include up to 10 files, at most 20 MB per file and 50 MB in total. Files are uploaded to the selected Node as Session-scoped Artifacts before the Run starts; the model receives only validated Artifact references.

Desktop preflights the public limits, but the selected Node is authoritative: it validates extension, MIME, content signature, archive structure, corruption, and bounded extraction before saving the original bytes. Supported message attachments are:

- Word `.docx`;
- Excel `.xlsx` and UTF-8 `.csv`;
- text-based `.pdf` (up to 300 pages; scanned files require OCR and are rejected clearly);
- PowerPoint `.pptx`;
- `.png`, `.jpg`, `.jpeg`, and `.webp` images;
- a bounded set of UTF-8 text/code formats including Markdown, JSON, YAML, Python, JavaScript, and TypeScript.

Legacy `.doc`, `.xls`, and `.ppt`, encrypted PDFs, scanned PDFs without extractable text, damaged or spoofed files, unsafe Office archives, and arbitrary binary formats are not accepted. The original attachment remains downloadable. Office, PDF, CSV, and text files are converted to a bounded deterministic text projection for the model; images retain validated bytes for a vision-capable model.

The right-side Artifacts section reads durable Node facts rather than inferring filenames from the transcript. Images, text/code, and audio have inline previews; other formats remain downloadable. A remote Node never receives an arbitrary path on the Desktop machine.

Forking a Session copies every Artifact key and version into the new Session. Permanently deleting a Session also removes its Session-scoped Artifacts; archiving retains them. Storage failures return stable errors without exposing Node paths or secret material.

## Development environment overrides

These are process startup and connection inputs, not Node business configuration:

| Variable | Purpose |
|---|---|
| `OPENTEAMWORK_ROOT` | Source checkout used to locate the Python virtual environment. |
| `OPENTEAMWORK_NODE_ROOT` | Explicit local Node root; defaults to `~/.openteamwork`. |
| `OPENTEAMWORK_CLIENT_API_BASE_URL` | Explicit Node endpoint for development. |
| `OPENTEAMWORK_CLIENT_API_TOKEN` | Bearer token for the selected endpoint. |
| `OPENTEAMWORK_CLIENT_API_HOST` | Managed local bind host; defaults to `127.0.0.1`. |
| `OPENTEAMWORK_CLIENT_API_PORT` | Managed local port; defaults to `18765`. |
| `OPENTEAMWORK_CLIENT_DEBUG` | Enable bounded client connection diagnostics. |

## Packaging

The current packaging target is an unsigned macOS Apple Silicon developer preview.

```bash
pnpm desktop:package:dir
pnpm desktop:package
pnpm desktop:checksum
```

Artifacts are written below `apps/desktop/release/` and are not committed. Verify the unpacked application or DMG preload before distribution:

```bash
cd apps/desktop
npm run verify:package
hdiutil verify release/OpenTeamwork-Desktop-0.6.0-mac-arm64.dmg
cd release
shasum -a 256 -c SHA256SUMS.txt
```

The current application is not signed or notarized and must not be described as a stable macOS release.

## Troubleshooting

### `ppx-client failed to initialize`

Rebuild the Electron preload and restart the application:

```bash
cd apps/desktop
npm run build
node scripts/verify-preload.mjs dist-electron/preload/index.cjs
```

For a packaged build, run `npm run verify:package` and confirm that exactly one parseable preload host API exposure exists in the ASAR.

### Port already in use

Inspect the listening process:

```bash
lsof -nP -iTCP:18765 -sTCP:LISTEN
```

Stop the unintended process or configure one consistent port in Node setup and Desktop Settings.

### Desktop cannot reach the Node

Check the backend independently:

```bash
otw operations health --url http://127.0.0.1:18765
```

For LAN mode, add `--token '<token>'`, verify the host firewall, and confirm the configured Node identity before saving the target. The current development build exposes the returned identity but does not yet implement a cryptographic pairing ceremony.

### Electron installation is incomplete

```bash
pnpm approve-builds
pnpm install
```

Then rebuild Desktop. Do not copy an incomplete `node_modules` tree into a packaged application.

## Contract changes

The shared protocol, schemas, and fixtures live in [contracts/client-api](../../contracts/client-api/README.md). Any client-visible change must update Python fixtures, the TypeScript client, Desktop tests, and protocol documentation together.
