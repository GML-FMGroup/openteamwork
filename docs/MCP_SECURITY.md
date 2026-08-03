# Extension and MCP Security

OpenPPX treats Plugin, App, MCP, and Skill installation as governed Node mutations. A resource becoming discoverable does not make it trusted, installed, enabled, or available to an active Run.

## Lifecycle

```text
discover -> stage -> validate -> preview -> confirm -> install -> enable -> test
```

- Source adapters copy content into a controlled staging directory.
- Validation checks identity, manifest, size, path, link, digest, dependency, ownership, prefix, and risk rules.
- Preview is read-only and returns a bounded client-safe summary plus content digest.
- Install must present the exact preview digest; high-risk changes also require confirmation.
- Enablement is Agent-specific and revision-safe.
- Active Runs retain an immutable snapshot; updates affect future Runtime instances.

## Source defenses

Local archives and directories reject:

- absolute paths, `..` traversal, platform-dependent separators, and drive-prefixed paths;
- symlink, hardlink, device, socket, FIFO, and other unsupported filesystem entries;
- duplicate archive names and inconsistent size/count/expansion limits;
- content that escapes the staged root;
- manifest identity or digest drift between preview and installation.

Git sources must be pinned to a fixed commit. A branch name cannot silently change installed content. Catalog adapters provide discovery and bytes; they do not bypass normal staging or validation.

Source locators, credentials embedded in locators, and backend exception text are excluded from ordinary inventory responses.

## Direct MCP resources

A directly managed `McpServer` supports:

- `stdio` with an executable, argv list, optional working directory, and bounded environment bindings;
- `streamable_http` or `sse` with a clean HTTP(S) URL and bounded header bindings.

Sensitive values use `SecretRef` bindings:

```json
{
  "kind": "secret",
  "secretRef": {"store": "system", "name": "service-token"},
  "prefix": "Bearer "
}
```

The Secret is resolved only when constructing the ADK MCP toolset. The persisted resource, extension inventory, readiness output, diagnostics, audit rows, and Runtime metadata contain only status or a reference-safe projection.

Remote URLs cannot contain user information, query credentials, or fragments. STDIO execution is argv-only; OpenPPX does not pass the declaration through a shell.

## MCP tool policy

Each MCP resource can define:

- an allowlist of tool names;
- a stable Agent-wide tool prefix;
- confirmation requirements;
- progress event projection;
- long-task proxy behavior and inline time budget;
- a declared external-job protocol for status, output, cancel, pause, resume, and checkpoints.

Tool prefixes must remain unique across direct MCP, Apps, and Product Plugins enabled for one Agent. A collision is rejected before ADK assembly.

OpenPPX does not infer remote-job semantics from arbitrary provider payloads. Long-task controls appear only when the resource declares the corresponding protocol and the contract probe succeeds.

## Apps

An App separates product identity and authorization from transport:

- `AppDefinition` declares branding, developer, category, auth slots, tool catalog, risk, and an MCP transport template.
- `AppConnection` binds a user-managed authorization instance, SecretRefs, selected tools, grants, and Agent enablement.

Connections may only narrow the definition's tool policy. High-risk tools require explicit confirmation. Updating a definition cannot invalidate an active referenced connection, and a Plugin-owned definition cannot be removed independently from its owner.

## Product Plugins

Product Plugin v1 is declarative. Its root manifest is `.openppx-plugin/plugin.json` and may reference:

- Skills;
- App definitions;
- MCP resources;
- Agent templates;
- object schemas;
- documentation;
- explicitly allowed runtime capability references.

It cannot declare credentials, CLI entry points, arbitrary Python or ADK initialization hooks, package installers, background processes, or unbounded host code. Referenced files must be unique safe relative paths below the Plugin root.

Plugin-owned resources are projected from immutable installed content. They are not copied into independently mutable registries, preventing ownership and update drift.

## Skills

Skills are instructions, references, and controlled scripts. Installation validates manifest identity, dependencies, digest, risk, and content boundaries. An Agent sees only its enabled immutable Skill snapshot.

Skill instructions are not a security boundary. Tool and sandbox policy remains authoritative even if a Skill requests broader behavior.

## Readiness and failure isolation

An extension can be installed but not ready. Readiness reports stable non-sensitive reasons such as:

- missing protected credential;
- unavailable executable;
- dependency not installed or enabled;
- owner not enabled;
- prefix or resource identity conflict;
- connection probe failure;
- policy restriction.

A missing credential or unavailable MCP omits only that affected toolset from a new Runtime and returns a redacted diagnostic. It does not expose the Secret or silently switch to an undeclared source.

## Permissions, confirmation, and audit

All lifecycle mutations execute through typed Actions and immutable `PolicyContext` evaluation. The caller must have the required capability, permission, and bound target scope. High-risk installation, enablement, reauthorization, or removal additionally requires explicit confirmation.

Audit facts record Action identity, actor, target, policy decision, outcome, and timestamps. Request bodies, response bodies, source bytes, SecretRefs, and Secret values are not stored. High-risk mutations fail closed if the audit start cannot be recorded.

## Runtime isolation

MCP servers and extension scripts still run with the operating-system authority of their configured process unless a sandbox policy is applied. Review local executables and source trust before enablement. For dangerous local execution, use the Docker policy described in [SANDBOX.md](./SANDBOX.md).

Access to the Docker daemon is itself trusted and host-powerful. Isolation does not make an unknown extension safe to install.

## Operational checklist

Before enabling an extension:

1. Confirm its source, fixed version or commit, and digest.
2. Review the declared resources, commands, endpoints, tools, risk, and requested runtime capabilities.
3. Confirm that every sensitive binding is a SecretRef.
4. Prefer a tool allowlist and the lowest useful Agent privilege.
5. Test readiness and MCP discovery before assigning real work.
6. Review Action audit and Task facts after the first run.
7. Disable the resource before removal; resolve active references explicitly.
