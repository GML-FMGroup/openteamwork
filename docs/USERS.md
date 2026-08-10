# Users and Remote App Access

OpenTeamwork separates Node administration from day-to-day App use. An administrator operates one Node and provisions local product accounts; each person signs in from Desktop with an email and secret and receives a revocable opaque session token.

## Authorization model

User and Agent privilege levels share one ordered scale:

```text
low < medium < high < root
```

- A user can create an Agent only at or below their own privilege level.
- The authenticated creator becomes the Agent owner. Non-root users can list, change, enable, remove, and use only their own Agents.
- `root` users can see and operate all Agents and own Node-level model, extension, connection, setup, Operations, and audit administration.
- Sessions, Runs, Artifacts, Goals, Tasks, and Automations are bound to the authenticated user. Supplying another `userId` does not change that identity.
- Agent sharing, ownership transfer, password changes, privilege changes, account re-enablement, and account deletion are outside the current MVP.

The older `ppx-client-user` identity remains only for trusted runtime and CLI compatibility. Product accounts are not migrated to or from it.

## Provision accounts on the Node machine

Account administration is local-only and does not have a remote HTTP endpoint. The secret is hidden by default and is never accepted as a positional command-line argument:

```bash
otw user add admin@example.com --privilege root
otw user add jiang@example.com --privilege high
otw user list
```

For non-interactive provisioning, send exactly one secret line over standard input:

```bash
printf '%s\n' "$NEW_USER_SECRET" | otw user add jiang@example.com \
  --privilege high \
  --secret-stdin
```

Do not place the secret directly in a shell command or deployment manifest. Secrets are hashed with Argon2id; the Node stores neither their plaintext nor App session tokens.

Disabling an account is permanent in this MVP and immediately revokes all of its App sessions:

```bash
otw user disable jiang@example.com --yes
```

All commands default to `~/.openteamwork`. Pass the same `--node-root` used by the Node when operating another root.

## Deploy the Node for a remote Desktop

Keep the Python Client API on loopback and put an HTTPS reverse proxy on the same machine. The deployment bearer token protects trusted non-user Client API callers; Desktop users never receive or enter it.

Configure the Node with authentication required even though it listens only on loopback:

```bash
otw setup \
  --node-root ~/.openteamwork \
  --listen-host 127.0.0.1 \
  --listen-port 18765 \
  --authentication required
```

This step writes only Node configuration and does not request an LLM API key. Create the first root account before starting the Node; after root signs in through Desktop, first-Agent onboarding collects the model configuration and protected provider credential.

Start it with a strong deployment token supplied through the process environment:

```bash
export OPENTEAMWORK_CLIENT_API_TOKEN='<strong-random-deployment-token>'
otw node run --node-root ~/.openteamwork
```

If a service manager starts the Node, supply `OPENTEAMWORK_CLIENT_API_TOKEN` through that manager's protected secret/environment mechanism. `otw node service install` writes a base manifest but intentionally does not copy secrets from the current shell into it.

Terminate TLS on the same host and proxy to `127.0.0.1:18765`. For example, a minimal Caddy site is:

```caddyfile
node.example.com {
    reverse_proxy 127.0.0.1:18765
}
```

The public side must be HTTPS with a certificate trusted by the Desktop machine. Do not expose port `18765`, proxy it over plaintext from another host, or terminate TLS on another machine: user session tokens are accepted only from a direct loopback peer, which is either the local Desktop or a same-host TLS proxy.

The reverse proxy must preserve the `Authorization` header and allow long-lived Server-Sent Events responses. Apply ordinary host firewall, certificate renewal, request-size, and monitoring policy at this boundary.

## Sign in from Desktop

On the Desktop machine:

1. Start OpenTeamwork Desktop.
2. Choose **Remote Node**.
3. Enter the HTTPS origin, for example `https://node.example.com`.
4. Enter the provisioned email and secret.

The secret is used only for the login request and is not saved. The returned opaque session token is encrypted by Electron `safeStorage`, bound to the exact Node origin and user ID, and never exposed to the Renderer. Signing out revokes the current token and removes its encrypted local copy.

User sessions expire after 30 days. A disabled account and an explicit sign-out revoke sessions immediately. If a token expires or cannot be decrypted on the current OS account, Desktop asks the user to sign in again.

## Backup and recovery

Account records and session revocation state live in `<node-root>/database/identity.db` alongside the runtime identity records. Stop the Node before taking a filesystem backup, then back up the entire Node root so SQLite WAL files, Config, workspaces, Artifacts, and databases stay consistent.

The current MVP has no password reset or account recovery workflow. Losing an active account secret requires provisioning a new account; disabling the old account revokes its sessions but does not transfer its Agents.
