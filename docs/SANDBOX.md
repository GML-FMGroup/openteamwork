# Sandbox

OpenPPX provides a Docker execution backend for dangerous local commands and declarative Skill APIs. Sandbox policy is a trusted Node deployment concern; model-authored recipes may request stricter isolation but cannot silently weaken the configured baseline.

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
- no network by default;
- bounded CPU, memory, PIDs, temporary storage, timeout, and output;
- labeled containers with best-effort cleanup after timeout, kill, or removal.

Background and PTY commands use the persistent process-session controls for polling, logs, input, cancellation, and cleanup.

## Trusted baseline

Set the minimum backend before starting the Node:

```bash
export OPENPPX_SANDBOX_BACKEND=docker
export OPENPPX_SANDBOX_IMAGE=openppx-sandbox:dev
ppx node run
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

## Operational cautions

- Review every host path made visible to a container.
- Keep the Docker socket outside the sandbox.
- Treat an enabled network and a writable workspace as meaningful privileges.
- Remove leaked containers by their OpenPPX labels only after inspecting active tasks.
- Sandbox evidence complements Action policy, confirmation, extension trust, and audit; it does not replace them.
