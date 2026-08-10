# Sandbox

OpenTeamwork provides a Docker execution backend for dangerous local commands and declarative Skill APIs. Sandbox policy is a trusted Node deployment concern; model-authored recipes may request stricter isolation but cannot silently weaken the configured baseline.

Docker is a pragmatic isolation layer, not a perfect security boundary. Anyone who controls the Docker daemon is effectively host-privileged.

## Build the image

From the repository root:

```bash
docker build \
  --tag openppx-sandbox:dev \
  --file docker/sandbox/Dockerfile \
  docker/sandbox
```

To use a mirrored Python base image:

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=registry.example/python:3.14-slim \
  --tag openppx-sandbox:dev \
  --file docker/sandbox/Dockerfile \
  docker/sandbox
```

Install Python or Node dependencies into a reviewed derived image at build time. Runtime recipes must not become arbitrary package installers.

## Execution behavior

Docker-sandboxed execution uses:

- the workspace mounted at the same absolute path;
- `.git` metadata mounted read-only;
- credential-style workspace files masked;
- no direct network by default;
- bounded CPU, memory, PIDs, temporary storage, timeout, and output;
- labeled containers with best-effort cleanup after timeout, kill, or removal.

Background and PTY commands use the persistent process-session controls for polling, logs, input, cancellation, and cleanup.

## Trusted baseline

Set the minimum backend before starting the Node:

```bash
export OPENPPX_SANDBOX_BACKEND=docker
export OPENPPX_SANDBOX_IMAGE=openppx-sandbox:dev
otw node run
```

`OPENPPX_SANDBOX_BACKEND=docker` prevents a tool request from downgrading to a weaker backend without the normal confirmation path.

To force declarative Command, Python, and Node Skill APIs into Docker even when their recipe does not opt in:

```bash
export OPENPPX_SKILL_API_SANDBOX=docker
```

Skill files are ordinary extension content and are not the security authority. Trusted Node policy wins over a recipe field.

## Network policy

Network is disabled by default. A recipe request for enabled networking is honored only when trusted deployment policy allows it:

```bash
export OPENPPX_SANDBOX_ALLOW_NETWORK=1
```

A hard lock overrides every recipe request:

```bash
export OPENPPX_SANDBOX_NETWORK_LOCK=disabled
```

Grant network access narrowly. A sandbox with network access can still exfiltrate any data made available inside it.

### Permission-derived proxy-only egress

When static Command permissions are enforced, `low`, `medium`, and `high` cannot select host execution:

- `low` runs its reviewed `grep`/`rg` commands in a read-only Workspace mount with network disabled;
- `medium` and `high` attach only to a Node-configured Docker `--internal` network;
- a trusted OpenTeamwork egress proxy is the only service connected to both that internal network and an external network;
- the proxy loads a revision-addressed, network-only policy and verifies a high-entropy credential for that exact revision;
- the task container receives its own proxy credential but cannot read the policy directory or select another revision without that revision's credential.

The Runtime publishes the current compatible permission revision immediately before each new proxy-backed command. A permission update therefore cannot reuse an older, wider egress policy for a later command.

Provisioning is an operator action. OpenTeamwork verifies the network but never creates or weakens it during an Agent call:

```bash
docker network create --internal openppx-egress-internal
```

Run `otw-egress-proxy` from a reviewed service image that has OpenTeamwork installed. Mount the Node-owned policy directory read-only, start the proxy on an external network, and then attach only that trusted proxy container to the internal network:

```bash
docker build \
  --tag openppx-egress-proxy:dev \
  --file docker/egress-proxy/Dockerfile \
  .

docker run -d \
  --name openppx-egress-proxy \
  --network bridge \
  --mount type=bind,src=/srv/openppx/egress-policies,dst=/policies,readonly \
  openppx-egress-proxy:dev \
  --policy-directory /policies --listen 0.0.0.0 --port 3128

docker network connect openppx-egress-internal openppx-egress-proxy
```

The matching Node configuration uses `http://openppx-egress-proxy:3128`, Docker network `openppx-egress-internal`, and host policy directory `/srv/openppx/egress-policies`. The directory is created with restrictive permissions and must stay outside every Agent Workspace.

For plain HTTP the proxy evaluates the actual method. For HTTPS CONNECT, the method is encrypted, so the proxy requires connect/read/write/upload permission together and blocks the whole origin if any of those actions is denied. Proxy audit logs contain Agent ID, permission revision, method, scheme, port, visibility, outcome, and reason code, but omit destination host, URL path, query, headers, and body.

## Image policy

Recipes cannot select arbitrary images. An image request must equal the configured default or match a trusted allowlist:

```bash
export OPENPPX_SANDBOX_IMAGE=openppx-sandbox:dev
export OPENPPX_SANDBOX_TRUSTED_IMAGES='registry.example/openppx-sandbox:*'
```

Keep the allowlist narrow and pin reviewed image digests for sensitive deployments.

## Resource policy

Trusted deployment settings:

| Variable | Default | Purpose |
|---|---|---|
| `OPENPPX_SANDBOX_BACKEND` | `none` | Minimum execution backend. |
| `OPENPPX_EXEC_SANDBOX` | unset | Optional default requested by direct command execution. |
| `OPENPPX_SKILL_API_SANDBOX` | unset | Minimum backend for declarative Skill APIs. |
| `OPENPPX_SANDBOX_DOCKER_BIN` | `docker` | Docker executable. |
| `OPENPPX_SANDBOX_IMAGE` | `openppx-sandbox:dev` | Default reviewed image. |
| `OPENPPX_SANDBOX_ALLOW_NETWORK` | unset | Allows an explicitly requested network-enabled recipe. |
| `OPENPPX_SANDBOX_NETWORK_LOCK` | unset | `disabled` locks network off. |
| `OPENPPX_SANDBOX_TRUSTED_IMAGES` | unset | Comma-separated trusted image patterns. |
| `OPENPPX_SANDBOX_TIMEOUT_MAX_SECONDS` | 60 for command execution | Trusted timeout cap. |
| `OPENPPX_SANDBOX_MEMORY` | `1024m` | Docker memory and memory-swap limit. |
| `OPENPPX_SANDBOX_CPUS` | `2` | Docker CPU limit. |
| `OPENPPX_SANDBOX_PIDS_LIMIT` | `256` | Process limit. |
| `OPENPPX_SANDBOX_TMPFS_SIZE` | `256m` | Temporary filesystem size. |

These environment values configure the trusted execution backend, not Node business resources. Long-term product controls can project the same policy through strict Node Config without changing the sandbox enforcement boundary.

## Recipe opt-in

A declarative Skill API may request Docker when the trusted baseline is weaker:

```json
{
  "module": "demo_sdk",
  "function": "search",
  "sandbox": {
    "required": true,
    "network": "disabled"
  }
}
```

Command recipes use the same field. Recipe arguments and API input are delivered as structured stdin, not as a large environment variable.

A recipe can request stricter limits or approved network/image settings only within the trusted policy. It cannot disable masking, add host mounts, request privileged mode, pass raw Docker flags, or select an untrusted image.

## Verification

Regular sandbox unit tests do not require Docker:

```bash
python -m pytest -q \
  tests/test_runtime_sandbox.py \
  tests/test_runtime_command_api_runner.py \
  tests/test_runtime_long_tasks.py \
  tests/test_tools.py
```

Real Docker tests are opt-in:

```bash
export OPENPPX_RUN_DOCKER_SANDBOX_TESTS=1
export OPENPPX_SANDBOX_IMAGE=openppx-sandbox:dev
python -m pytest -q tests/test_docker_sandbox_integration.py
```

The integration suite verifies workspace masking, read-only Git metadata, default network denial, PTY/background cleanup, Skill API execution, and trusted network/image controls.

After building both images, the opt-in egress integration creates two temporary Docker networks and verifies that a task can reach a target through the revision-bound proxy but cannot connect to the target directly:

```bash
export OPENPPX_RUN_DOCKER_EGRESS_TESTS=1
export OPENPPX_SANDBOX_IMAGE=openppx-sandbox:dev
export OPENPPX_EGRESS_PROXY_IMAGE=openppx-egress-proxy:dev
python -m pytest -q tests/runtime/test_proxy_egress_docker_integration.py
```

The test removes its named containers and networks in a `finally` block.

## Operational cautions

- Review every host path made visible to a container.
- Keep the Docker socket outside the sandbox.
- Treat an enabled network and a writable workspace as meaningful privileges.
- Remove leaked containers by their OpenTeamwork labels only after inspecting active tasks.
- Sandbox evidence complements Action policy, confirmation, extension trust, and audit; it does not replace them.
