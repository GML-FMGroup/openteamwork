# Static execution permissions

OpenTeamwork compiles each Agent's privilege preset, Node-owned ceilings, Agent-owned rules, and rollout settings into one content-addressed permission snapshot. A Runtime retains its Agent and Workspace identity boundary, while compatible permission-only updates are re-resolved before every new Tool Action and side effect. Model arguments never select the effective privilege level.

This version covers five execution surfaces: the Agent Workspace, external paths, Command/Process, Network, and Tool/Action. Delegation, secrets, high-risk confirmation, task ownership, and future dynamic elevation remain separate controls and are intersected with this matrix.

## Built-in presets

| Preset | Own Workspace | External paths | Command / Process | Network | Tool / Action |
|---|---|---|---|---|---|
| `low` | Read, list, and search only | Denied | Only direct `grep`/`rg` searches in a read-only Docker-mounted Workspace; no Shell, PTY, or background process | Whitelist model with an empty default whitelist | Reviewed read-oriented Tool allowlist |
| `medium` | Read/write/execute task files | Denied, except Node-declared safe read roots; high/root Workspaces always denied | Current-task commands and processes in Docker | Public destinations allowed unless denied; private/control-plane access and listening denied | Allowed except Node maintenance Tools and explicit denies |
| `high` | Read/write/execute | Read all; mutation requires explicit grants and cannot cover Node/OS protected roots | Docker execution and non-system process management | Public destinations allowed unless denied; private/control-plane access and listening denied | Allowed except explicit denies |
| `root` | Unrestricted | Unrestricted | Host execution and system process authority | Unrestricted by this matrix | Unrestricted by this matrix |

The preset is a starting template, not the final decision. Node hard-deny rules, Agent rules, non-execution controls, supported constraints, path boundaries, process provenance, network facts, and sandbox capability are combined with deny precedence. A Tool call succeeds only when every applicable layer allows it.

## Rule semantics

Permissions use an object/action default plus ordered matching facts:

- objects: `workspace`, `external_path`, `command`, `process`, `network`, and `tool`;
- effects: `allow` or `deny`;
- selectors: relative Workspace patterns, absolute external roots, Agent Workspace ownership, structured command identity, trusted process provenance, normalized network targets, or stable Tool IDs;
- constraints: object-specific limits such as command profile, timeout, output cap, current-task ownership, or Tool operation;
- precedence: any matching deny wins; otherwise a matching allow wins; otherwise the explicit action default is used.

Node `hardRules` are locked ceilings. Agent rules may specialize a preset but cannot override a matching Node or locked preset deny.

Path size/count/depth, Command profile/process limits, Process task/Agent ownership, and managed/read-only Network constraints are enforced by their adapters. A constraint is accepted only when an existing adapter enforces it; unsupported fields such as Network `maxResponseBytes` or Tool `parameterProfile` fail strict Config validation. Constraints are obligations on allow rules and are rejected on deny rules.

## Rollout modes

Each object rolls out independently:

- `observe`: evaluate and record the matrix decision without making that decision authoritative;
- `enforce`: make the new decision authoritative and fail closed if its required boundary is unavailable.

An Agent may also set `spec.permissions.rolloutMode` to `observe` or `enforce` as a total override for all six permission objects. When this field is absent, rollout resolves per object in this order: Agent `rolloutModes`, Node `rolloutModes`, then the default `observe`. A configured total override wins over both per-object maps but does not erase them, so removing the override restores the previous fine-grained policy.

Use the Agent-wide override when one Agent is ready to move entirely into observation or enforcement. Use per-object modes for staged calibration. Move one object at a time from `observe` to `enforce`, inspect permission audit results, then continue. Switching an object or the Agent-wide override back to `observe` is the immediate policy rollback and is rechecked by long-lived compatible Runtimes before their next Tool Action.

Permission decisions are stored in `<node-root>/database/permission_audit.db`. The records contain decision facts and rule/revision identifiers but omit Tool arguments and raw path or URL values. During a canary rollout, inspect recent results with:

```bash
sqlite3 <node-root>/database/permission_audit.db \
  "SELECT recorded_at, agent_id, object_kind, action, outcome, rollout_mode, enforced, reason_code FROM permission_audit ORDER BY audit_id DESC LIMIT 50;"
```

The same redacted records are available through the authenticated Control Plane Action:

```bash
otw action invoke permissions.audit.list --input-json '{"limit":50}'
```

## Enforcement boundaries

- Google ADK's `BasePlugin.before_tool_callback` is the common Tool invocation gate for built-ins, extensions, MCP Tools, and native App Tools.
- Path, Command, Process, and Network adapters authorize trusted runtime facts at the actual executor. The ADK callback is not treated as an operating-system boundary.
- File opens use canonical paths, hard-link restrictions for non-root presets, no-symlink descriptor walks, and inode revalidation.
- Enforced Commands run with a permission-derived profile. `low`, `medium`, and `high` require Docker; callers cannot request a weaker backend.
- Process sessions retain Agent, task, Run, permission revision, execution profile, and command provenance. Later process actions are authorized from those trusted facts rather than a caller-supplied scope.
- Managed HTTP requests authorize normalized URL, current DNS/IP visibility, and every redirect.
- Arbitrary code for `medium` and `high` uses a proxy-only Docker network. Task containers have no direct route, and the proxy requires a high-entropy credential bound to the exact permission revision.

For HTTPS, the proxy can inspect the destination but not the encrypted HTTP method. It therefore requires `connect`, `read`, `write`, and `upload` to all be allowed before opening a CONNECT tunnel. An action-specific deny safely blocks the entire HTTPS origin.

## Runtime update and revocation semantics

Before every new Tool invocation, OpenTeamwork resolves the current Agent permission snapshot from the trusted Config service. Built-in side-effect adapters use the same snapshot pinned for that one Tool Action. The ADK authorization Plugin also reauthorizes extension, MCP, App, and built-in Tool calls with the current revision. If Config storage is unavailable, or the Agent/Workspace identity no longer matches the assembled Runtime, the invocation fails closed.

Permission tightening therefore blocks the next Tool Action even in a long-lived Runtime. A process that has already started is not retroactively erased, but its next view, input, wait, stop, or cleanup request is authorized against the current Process policy and immutable process provenance. A newly started proxy-backed command publishes and uses the current Network policy revision.

Permission widening does not inject newly eligible Tools, MCP servers, Apps, Models, or extension content into an existing Runtime. Assemble a new Runtime to expand those catalogs. This asymmetry is deliberate: revocation is immediate at the next boundary, while authority expansion requires a fresh trusted assembly.

## Control Plane Action boundary

The Agent execution matrix governs Agent-initiated Tool calls and the resources those Tools reach. Authenticated user and operator calls to the Control Plane are a separate authority domain: `ActionPolicy` evaluates the caller's capabilities, permissions, bound scope, and required confirmation. An Agent's `low`, `medium`, `high`, or `root` preset does not grant or remove an operator's Config authority.

If an Agent reaches an external effect through a Tool, both the Tool invocation rule and the relevant Workspace, external-path, Command/Process, or Network rule must allow it. The effective authority is always the intersection of applicable layers.

## Clean configuration boundary

This release intentionally provides no execution-permission migration or compatibility parser. Rebuild Agent resources with `privilegeLevel`, `permissions`, and optional non-execution `controls`. Unknown or removed fields fail validation. Before starting this version against a development Node that previously used an earlier permission-audit schema, rebuild that Node data directory rather than mixing schemas.

Use Config validation and preview before apply. Preview reports semantic default, rule, rollout, and deployment-gate changes without exposing configured paths or resource values.

## Recommended rollout

1. Rebuild Agent resources with the static matrix as their only execution-permission source, then resolve every reported deployment gate.
2. Configure Node safe/protected roots and the code-egress proxy before enforcing Command for `medium` or `high`.
3. Start with Tool and Workspace in `observe`; compare task success and observed deny rates.
4. Enforce one canary Agent, then expand Workspace, external path, Command/Process, and managed Network separately.
5. Enable proxy-only code egress last, after its internal Docker network and trusted proxy service pass the integration test.

Dynamic elevation and human approval are intentionally not part of this static release. They require authenticated operator identity, approval scope and expiry, replay protection, revocation, durable audit, and multi-client UX. Static rollout modes provide a safe feedback loop without pretending those wider requirements are solved.
