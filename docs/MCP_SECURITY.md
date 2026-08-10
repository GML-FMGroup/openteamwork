# Extension and MCP Security

OpenTeamwork treats Plugin, App, MCP, and Skill installation as governed Node mutations. A resource becoming discoverable does not make it trusted, installed, enabled, or available to an active Run.

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

Remote URLs cannot contain user information, query credentials, or fragments. STDIO execution is argv-only; OpenTeamwork does not pass the declaration through a shell.

## MCP tool policy

Each MCP resource can define:

- an allowlist of tool names;
- a stable Agent-wide tool prefix;
- confirmation requirements;
- progress event projection;
- long-task proxy behavior and inline time budget;
- a declared external-job protocol for status, output, cancel, pause, resume, and checkpoints.
- optional MCP Resource access with an exact URI allowlist.

Tool prefixes must remain unique across direct MCP, Apps, and Product Plugins enabled for one Agent. A collision is rejected before ADK assembly.

OpenTeamwork does not infer remote-job semantics from arbitrary provider payloads. Long-task controls appear only when the resource declares the corresponding protocol and the contract probe succeeds.

## MCP Resources

MCP Resource access is disabled by default. Enabling it requires both fields in the same strict policy:

```json
{
  "resourcesEnabled": true,
  "resourceUriAllowlist": [
    "resource://company-handbook/approved"
  ]
}
```

The allowlist contains at most 256 unique absolute URIs and uses exact matching. OpenTeamwork filters both Resource discovery and Resource reads, so a model cannot bypass discovery by constructing a known Resource name. The generated ADK Tool remains namespaced with the MCP prefix, for example `mcp_docs_load_mcp_resource`, and therefore also passes through the ordinary Tool permission gate.

OpenTeamwork reuses the authenticated Google ADK MCP session. Static transport Secrets, the previously authorized Network endpoint, and dynamic user/session/tenant headers remain in force for Resource list and read operations. Resource access does not create a second ungoverned client.

The current safe projection accepts text Resources up to 1 MB per read. Binary Resource content is rejected before model-context insertion. A provider should return downloadable binary content from an MCP Tool so OpenTeamwork can validate and persist it as an Artifact instead.

## MCP binary results

Google ADK MCP Tools are wrapped before the long-task proxy. Supported MCP `ImageContent` and embedded binary Resource results are:

1. bounded before base64 decoding;
2. decoded with strict base64 validation;
3. limited to 20 MB;
4. validated against the declared MIME type and supported attachment format;
5. saved through the active ADK ToolContext as a Session-scoped Artifact;
6. replaced in the model-facing FunctionResponse by a small Artifact reference.

This ordering is identical for inline and background MCP completion. Invalid, unsupported, oversized, or unavailable-storage results are omitted with a bounded error; their original base64 is never returned to the model. Audio and arbitrary opaque binaries are not persisted by this first implementation unless they enter a separately reviewed supported-format boundary.

## Apps

An App separates public product identity and authorization from execution:

- `AppDefinition` declares branding, developer, category, auth slots, tool catalog, risk, and exactly one implementation.
- `AppConnection` binds a user-managed authorization instance, SecretRefs, selected tools, grants, and Agent enablement.
- An MCP implementation points at a product's standard MCP service; a native implementation names a trusted adapter compiled into the Node.

Native adapter names in configuration never load arbitrary code. Only adapters explicitly registered by the Node can resolve Secrets and construct ADK tools. Connections may only narrow the definition's tool policy, and high-risk tools require explicit confirmation.

## Product Plugins

OpenTeamwork uses the Codex Plugin package contract with one portable root alias: the manifest is `.agent-plugin/plugin.json` instead of `.codex-plugin/plugin.json`. All manifest fields and component formats otherwise follow the Codex Plugin standard. A Plugin may contain:

- `skills/` with standard `SKILL.md` packages;
- `.mcp.json` with standard MCP server definitions;
- `.app.json` with registered App connector IDs;
- `hooks/` declarations;
- `assets/` referenced by Plugin metadata.

The package is not allowed to use the removed product-private schema, inject arbitrary Python or ADK initialization code, or persist credential values. Referenced files must be safe relative paths below the Plugin root. Hooks are parsed and reported as unsupported until a reviewed hook runtime is available; they are never executed implicitly.

Plugin components are projected from immutable installed content into internal Skill, MCP, and registered-App views. Internal resource IDs are namespaced without rewriting the portable package on disk.

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
5. Keep MCP Resources disabled unless exact reviewed URIs are required.
6. Test readiness and MCP discovery before assigning real work.
7. Review Action audit, Task facts, and any created Artifacts after the first run.
8. Disable the resource before removal; resolve active references explicitly.
