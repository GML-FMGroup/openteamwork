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
    "controls": {},
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

The supported privilege levels are `low`, `medium`, `high`, and `root`. Static execution policy has one source of truth: `permissions`. The separate `controls` object contains only non-execution controls for secret access, delegation, privilege-approval authority, and high-risk Actions. Removed execution-override fields are rejected rather than translated or silently ignored.

Agent-owned static rules and canary rollout settings are declared explicitly:

```json
{
  "spec": {
    "privilegeLevel": "low",
    "permissions": {
      "rolloutModes": {
        "workspace": "enforce",
        "external_path": "observe",
        "command": "observe",
        "process": "observe",
        "network": "observe",
        "tool": "enforce"
      },
      "rules": [
        {
          "ruleId": "allow-customer-note-drafts",
          "effect": "allow",
          "object": "workspace",
          "actions": ["create", "write", "edit"],
          "selector": {
            "kind": "workspace_path",
            "patterns": ["notes/**"]
          }
        }
      ]
    }
  }
}
```

Node-owned ceilings, shared roots, and arbitrary-code egress are configured in `NodeConfig.spec.permissions`:

```json
{
  "spec": {
    "permissions": {
      "safeExternalReadRoots": ["/srv/openppx/reference"],
      "highProtectedWriteRoots": ["/etc", "/srv/openppx/node-config"],
      "rolloutModes": {
        "workspace": "observe",
        "external_path": "observe",
        "command": "observe",
        "process": "observe",
        "network": "observe",
        "tool": "observe"
      },
      "codeEgressProxy": {
        "url": "http://openppx-egress-proxy:3128",
        "dockerNetwork": "openppx-egress-internal",
        "policyDirectory": "/srv/openppx/egress-policies"
      },
      "hardRules": []
    }
  }
}
```

Rollout defaults to `observe`. Agent rollout values override the Node default for that Agent. Enforce mode fails closed when a required protected root, Docker backend, proxy policy, or internal network is unavailable. The policy directory must be Node-owned, mode `0700`, and outside every Agent Workspace.

See [PERMISSIONS.md](./PERMISSIONS.md) for the preset matrix, selectors, precedence, clean-rebuild requirement, and recommended rollout order.

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

For an Agent resource, add `--agent <agent-id>`. Preview returns a redacted structural diff and lifecycle effects. Apply revalidates the candidate and expected revision, performs an atomic replace, and reports whether future Runs, a reload, or a Node restart is required. A permission change still reports `next_run` when a fresh Runtime is needed to finish catalog or identity changes; compatible permission tightening is additionally rechecked by an existing Runtime before its next Tool Action.

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

Window layout, panel widths, and saved connection targets belong to the current Desktop device, not Node Config. Electron Main stores a versioned target collection with one explicit active target. Every LAN bearer token is encrypted separately and bound to its exact endpoint; the ordinary connection file stores only a credential reference. The Renderer can list non-secret target metadata but cannot read token values.

## Failure behavior

- Invalid JSON, unknown fields, invalid paths, or wrong types produce structured diagnostics.
- A stale or missing `expectedRevision` rejects mutation; there is no last-writer-wins fallback.
- Atomic write failure leaves the previous valid resource in place.
- Missing credentials affect readiness without leaking which value was expected.
- A Run pins Config identity, Model, and Extension snapshots. Compatible permission-only updates are rechecked before every new Tool Action and side effect; identity, Workspace, Model, and extension-catalog changes require a later Runtime instance.
