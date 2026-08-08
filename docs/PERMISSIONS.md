# Static execution permissions

OpenPPX compiles each Agent's privilege preset, Node-owned ceilings, Agent-owned rules, and rollout settings into one immutable, content-addressed permission snapshot. A Run pins that snapshot. Tool catalog filtering and every side-effect adapter use the same revision; model arguments never select the effective privilege level.

This version covers five execution surfaces: the Agent Workspace, external paths, Command/Process, Network, and Tool/Action. Delegation, secrets, high-risk confirmation, task ownership, and future dynamic elevation remain separate controls and are intersected with this matrix.

## Built-in presets

| Preset | Own Workspace | External paths | Command / Process | Network | Tool / Action |
|---|---|---|---|---|---|
| `low` | Read, list, and search only | Denied | Only direct `grep`/`rg` searches in a read-only Docker-mounted Workspace; no Shell, PTY, or background process | Whitelist model with an empty default whitelist | Reviewed read-oriented Tool allowlist |
| `medium` | Read/write/execute task files | Denied, except Node-declared safe read roots; high/root Workspaces always denied | Current-task commands and processes in Docker | Public destinations allowed unless denied; private/control-plane access and listening denied | Allowed except Node maintenance Tools and explicit denies |
| `high` | Read/write/execute | Read all; mutation requires explicit grants and cannot cover Node/OS protected roots | Docker execution and non-system process management | Public destinations allowed unless denied; private/control-plane access and listening denied | Allowed except explicit denies |
| `root` | Unrestricted | Unrestricted | Host execution and system process authority | Unrestricted by this matrix | Unrestricted by this matrix |

The preset is a starting template, not the final decision. Node hard-deny rules, Agent rules, legacy safety controls, Tool constraints, path boundaries, process provenance, network facts, and sandbox capability are combined with deny precedence. A Tool call succeeds only when every applicable layer allows it.

## Rule semantics

Permissions use an object/action default plus ordered matching facts:

- objects: `workspace`, `external_path`, `command`, `process`, `network`, and `tool`;
- effects: `allow` or `deny`;
- selectors: relative Workspace patterns, absolute external roots, Agent Workspace ownership, structured command identity, trusted process provenance, normalized network targets, or stable Tool IDs;
- constraints: object-specific limits such as command profile, timeout, output cap, current-task ownership, or Tool operation;
- precedence: any matching deny wins; otherwise a matching allow wins; otherwise the explicit action default is used.

Node `hardRules` are locked ceilings. Agent rules may specialize a preset but cannot override a matching Node or locked preset deny.

Path size/count/depth, Command profile/process limits, Process task/Agent ownership, and managed/read-only Network constraints are enforced by their adapters. A Network `maxResponseBytes` constraint or Tool `parameterProfile` is accepted for forward-compatible configuration but becomes an explicit blocking gate in enforce mode until a named runtime adapter exists; it is never silently ignored. Constraints are obligations on allow rules and are rejected on deny rules.

## Rollout modes

Each object rolls out independently:

- `legacy`: retain the previous runtime behavior while preserving the compiled snapshot;
- `observe`: run the previous behavior and record the new decision, including shadow mismatches;
- `enforce`: make the new decision authoritative and fail closed if its required boundary is unavailable.

The default is `observe`. Node rollout settings provide the fleet default and an Agent setting may select a canary mode for that Agent. Move one object at a time from `observe` to `enforce`, inspect permission audit results, then continue. Switching the object back to `observe` is the immediate rollback and affects newly assembled Runs.

Permission decisions are stored in `<node-root>/database/permission_audit.db`. The records contain decision facts and rule/revision identifiers but omit Tool arguments and raw path or URL values. During a canary rollout, inspect recent results with:

```bash
sqlite3 <node-root>/database/permission_audit.db \
  "SELECT recorded_at, agent_id, object_kind, action, outcome, rollout_mode, shadow_mismatch, reason_code FROM permission_audit ORDER BY audit_id DESC LIMIT 50;"
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

## Migration

The five legacy execution fields in `permissionOverrides` cannot be mixed with non-empty static execution rules. `migrate_legacy_execution_permissions(agent)` converts `workspaceScope`, `filesystemAccess`, `shellExec`, `networkAccess`, and `toolAccess` into a non-widening Agent permission overlay. It leaves unrelated legacy controls intact and does not enable enforcement.

Use Config validation and preview before apply. Preview reports semantic default, rule, and blocking-gate changes without exposing configured paths or resource values. Existing Runs keep their pinned revision; only new Runtime instances receive the new policy.

## Recommended rollout

1. Migrate legacy execution overrides and resolve every reported blocking gate.
2. Configure Node safe/protected roots and the code-egress proxy before enforcing Command for `medium` or `high`.
3. Start with Tool and Workspace in `observe`; compare task success and shadow-deny rates.
4. Enforce one canary Agent, then expand Workspace, external path, Command/Process, and managed Network separately.
5. Enable proxy-only code egress last, after its internal Docker network and trusted proxy service pass the integration test.

Dynamic elevation and human approval are intentionally not part of this static release. They require authenticated operator identity, approval scope and expiry, replay protection, revocation, durable audit, and multi-client UX. Static rollout modes provide a safe feedback loop without pretending those wider requirements are solved.
