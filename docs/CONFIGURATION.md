# Configuration and Model Profiles

OpenPPX configuration is a Node-owned set of strict, versioned resources. CLI, Desktop, and future clients validate and apply the same resource models through Actions; they do not implement their own persistence rules.

## Node root

The default local Node root is `~/.openppx`. Every Node process owns one explicit root, selected with `--node-root` or `OPENPPX_NODE_ROOT` at the Desktop process boundary.

Important locations:

```text
<node-root>/node.json
<node-root>/agents/<agent-id>/agent.json
<node-root>/model-profiles/<profile-id>/profile.json
<node-root>/extensions/
<node-root>/database/
<node-root>/artifacts/
<node-root>/memory/
<node-root>/logs/
```

Resource identifiers use lowercase letters, digits, and hyphens, are at most 63 characters, and cannot escape the Node root.

## Resource contract

All persisted Config resources:

- use `apiVersion: openppx.io/v1alpha1`;
- declare an exact `kind`;
- use strict camel-case fields;
- reject unknown fields and type coercion;
- have a canonical content revision;
- require the expected current revision for mutation;
- are written atomically under a stable file lock.

There is no alternate JSON or environment-based business configuration source.

## First setup

The preferred way to create the first resources is:

```bash
ppx setup \
  --node-root ~/.openppx \
  --provider google \
  --model <provider-model-id> \
  --agent-id main \
  --workspace <workspace-directory>
```

The flow performs:

1. `setup.status` against an empty or existing Node root;
2. strict Node, Agent, Model Profile, and optional Secret validation;
3. revision-safe `setup.apply`;
4. a real Google ADK model turn through `setup.hello`;
5. a durable verification record bound to the exact resource revisions.

`configured` means resources were applied. `ready` means those same revisions completed the first real model turn.

## NodeConfig

Minimal example:

```json
{
  "apiVersion": "openppx.io/v1alpha1",
  "kind": "NodeConfig",
  "metadata": {"name": "local-node"},
  "spec": {
    "displayName": "My OpenPPX Node",
    "enabledAgents": ["main"],
    "clientApi": {
      "listenHost": "127.0.0.1",
      "port": 18765,
      "authentication": "disabled"
    },
    "operations": {
      "taskSchedulerEnabled": true,
      "cronEnabled": true,
      "heartbeat": {
        "enabled": false,
        "everySeconds": 1800,
        "prompt": "Review current tasks and report only information that needs operator attention.",
        "activeHours": {"timezone": "user"}
      }
    }
  }
}
```

A non-loopback listener must use `authentication: required`, and the running process must receive a bearer token. Listener overrides passed to `ppx node run` are process-level deployment inputs; they do not silently rewrite `node.json`.

## AgentConfig

Minimal example:

```json
{
  "apiVersion": "openppx.io/v1alpha1",
  "kind": "AgentConfig",
  "metadata": {"name": "main"},
  "spec": {
    "displayName": "Main",
    "workspace": "/path/to/workspace",
    "ownerPrincipalId": "ppx-client-user",
    "privilegeLevel": "medium",
    "permissionOverrides": {},
    "modelPolicy": {
      "defaultProfile": "primary",
      "roleProfiles": {
        "fast": "fast-model",
        "reasoning": "reasoning-model"
      }
    }
  }
}
```

The supported privilege levels are `low`, `medium`, `high`, and `root`. Permission overrides can only narrow the selected base profile; they cannot silently grant broader access.

## ModelProfile

```json
{
  "apiVersion": "openppx.io/v1alpha1",
  "kind": "ModelProfile",
  "metadata": {"name": "primary"},
  "spec": {
    "displayName": "Primary",
    "provider": "google",
    "model": "<provider-model-id>",
    "credential": {"store": "system", "name": "primary-model-api-key"},
    "executionLocation": "remote",
    "capabilities": ["text", "tool_calling"],
    "fallbackProfiles": [],
    "enabled": true
  }
}
```

Profiles separate provider capability from runtime selection. Agent default, workload role (`fast`, `reasoning`, or `vision`), and an explicit per-Run override are resolved deterministically. Fallbacks are ordered, cycle-checked, and never bypass capability, credential, privacy, context, or cost constraints.

## Secrets

Persisted resources contain only a `SecretRef`:

```json
{"store": "system", "name": "primary-model-api-key"}
```

The local production backend uses the system credential store. If a secure backend is unavailable, readiness reports that state and fails closed; OpenPPX does not store the value in ordinary JSON.

Secret values may enter `ppx setup` or a protected Secret Action, but they are excluded from Config reads, diffs, diagnostics, audit facts, error text, and client responses.

## Read, validate, preview, and apply

Commands operate on the running Node through the shared Action boundary:

```bash
ppx config read
ppx config read --agent main

ppx config validate candidate-node.json
ppx config preview candidate-node.json --expected-revision <revision>
ppx config apply candidate-node.json --expected-revision <revision>
```

For an Agent resource, add `--agent <agent-id>`. Preview returns a redacted structural diff and lifecycle effects. Apply revalidates the candidate and expected revision, performs an atomic replace, and reports whether future Runs, a reload, or a Node restart is required.

## Model commands

```bash
ppx model list
ppx model read primary
ppx model readiness main --role reasoning
ppx model select main --role reasoning
ppx model apply primary profile.json --expected-revision <revision>
```

Use `--url`, `--token`, and `--json` when managing another Node or automating the command.

## Client-local settings

Window layout, panel widths, and a saved connection target belong to the current Desktop device, not Node Config. LAN bearer tokens are encrypted by Electron Main and the ordinary connection file stores only a reference bound to the endpoint.

## Failure behavior

- Invalid JSON, unknown fields, invalid paths, or wrong types produce structured diagnostics.
- A stale or missing `expectedRevision` rejects mutation; there is no last-writer-wins fallback.
- Atomic write failure leaves the previous valid resource in place.
- Missing credentials affect readiness without leaking which value was expected.
- A Run pins an immutable Config, Model, and Extension snapshot; updates affect later Runtime instances.
