# Project Architecture

OpenPPX is a Node-centered personal Agent platform built on Google ADK. The architecture separates durable backend facts from client interaction so a Desktop on one machine can manage Agents running on another trusted machine.

## Product boundary

```text
Clients
  CLI | Desktop | future Mobile
             |
Shared Client Contract
  identity | auth | actions | content | events
             |
OpenPPX Node
  Control Plane | Config | Models | Extensions | Operations
             |
Runtime Supervisor
  immutable Config/Model/Extension snapshot per runtime
             |
Google ADK
  Agent | Runner | Session | Artifact | Memory | MCP | Plugins
```

The Node is the sole online owner of Agent state. A client is free to present a different interaction model, but it cannot create a second business implementation.

## Dependency direction

1. Config, modeling, extensions, and Action domain models do not depend on HTTP, Electron, argparse, or an ADK Runner.
2. Runtime depends on validated immutable snapshots and adapts them to Google ADK; domains do not depend back on Runtime.
3. Control Plane composes domain services and Runtime operations without implementing transport rendering.
4. Client API maps identity, authorization, envelopes, HTTP, and SSE to the same application boundary.
5. CLI and Desktop consume Actions or versioned client operations. They do not read Node stores.
6. Future clients must use the same contract and cannot require Electron IPC or local filesystem access.

Architecture tests reject the return of deleted parallel paths, deprecated entry points, old configuration lookups, production fixture fallbacks, and transport-specific message tools.

## Node composition

`OpenPpxNodeHost` builds one object graph:

```text
OpenPpxNodeHost
  ClientApiHttpServer
  ClientApiCoordinator
  ControlPlaneApplication
    ConfigService
    SetupService
    Action Registry + Executor + Policy
    Extension Registry
    OperationsService
  NodeRuntimeSupervisor
    RuntimeAssembler
    ADK runtime cache
  NodeOperationsRuntime
    Task scheduler
    Cron
    Heartbeat
```

Startup initializes Node-owned automation before serving requests. Shutdown stops request acceptance, automation, extension connections, and runtimes in reverse order.

## Config and models

Strict resources are rooted under one explicit Node directory:

- `NodeConfig` owns identity, listener, enabled Agents, and automation policy.
- `AgentConfig` owns workspace, principal, privilege, permission narrowing, and model assignments.
- `ModelProfile` owns provider/model identity, SecretRef, execution location, capabilities, constraints, and explicit fallbacks.

Repositories provide atomic writes, canonical revisions, optimistic conflict detection, and structured redacted diagnostics. Runtime never projects these resources into process-wide environment variables.

## Extension Platform

Four product concepts retain separate lifecycle ownership:

- Product Plugin: declarative versioned bundle;
- App: managed external service and authorization state;
- MCP: direct protocol server resource;
- Skill: Agent instructions, references, and controlled scripts.

A read-only Extension Registry gives clients one inventory while domain managers remain the mutation authorities. Source adapters acquire content into a controlled staging area, validate paths and digests, and publish immutable installed content only after preview and confirmation.

Runtime assembly merges Agent-enabled direct resources and Plugin projections into one immutable extension snapshot. An update changes future runtime cache identity; active Runs retain their pinned content.

## Actions and policy

An Action is a stable typed operation with:

- identity and namespace;
- strict input model;
- required capability and permission;
- scope bindings;
- risk and confirmation policy;
- client projection metadata;
- a domain handler.

Execution order is deterministic: lookup, availability, input validation, policy evaluation, confirmation, audit start, handler, redacted result, audit outcome. High-risk mutation fails closed if its audit start cannot be recorded.

Slash commands are projections of this registry, not a separate command catalog. CLI direct Action invocation and Desktop controls use the same IDs and errors.

## Runtime and Run flow

```text
Client creates/chooses Session
  -> Node resolves Config and Model Profile
  -> Runtime Supervisor captures extension snapshot
  -> Runtime Assembler builds or reuses ADK runtime
  -> client starts Run
  -> ADK emits model/tool events
  -> Node projects ordered SSE events
  -> durable Session/Task/Artifact/Audit facts are updated
```

SSE reconnect uses event sequence and `Last-Event-ID` replay. Run cancellation is cooperative and registered with the Node-owned supervisor. Empty final replies and ADK error events fail the Run rather than becoming successful blank messages.

## Sessions, tasks, and artifacts

- ADK Session storage is Node-local and persists across Node restart.
- User-visible history is projected from ADK events and supports ADK-native rewind markers.
- `TaskRun`, `TaskEvent`, tool-call, checkpoint, artifact, delivery, and summary facts cover supervised long work.
- Runner capabilities determine whether cancel, pause, resume, checkpoint, or restart-from-boundary is honestly available.
- Task completion requires runner evidence; model prose is not completion proof.

## Operations

Task scheduling, Cron, and Heartbeat are lifecycle children of Node. Usage, health, and audit are exposed through transport-neutral Operations Actions. Desktop and CLI read the same projections.

Cron jobs carry only message, Agent, and user identity. A Cron invocation becomes a normal TaskRun. Heartbeat uses typed Node Config, active hours, and a busy guard.

## Client API

The Client API provides:

- public protocol/liveness negotiation;
- protected Node identity and capability metadata;
- caller-aware Action catalog and invocation;
- Agent, Session, Message, Run, and event operations;
- HTTP JSON and SSE with one bearer authentication boundary.

Canonical schemas and fixtures are shared by Python and TypeScript tests. The current development baseline has no pre-release compatibility obligation; compatibility commitments begin after the first public stable contract.

## Desktop

Electron Main is the trusted desktop boundary. It owns:

- local Node process supervision;
- LAN credential encryption through `safeStorage`;
- Client API connection, cache, SSE, and retry services;
- typed preload IPC validation.

The React Renderer owns presentation and device-local preferences. It cannot access Node credentials or stores.

## Security properties

- Secret values are resolved only at the final SDK/connection boundary and never persisted in ordinary resource JSON.
- Non-loopback listeners require bearer authentication.
- Extension paths, archives, references, prefixes, ownership, risk, and content digests are validated before enablement.
- Product Plugins are declarative and cannot register arbitrary Python initialization hooks or package installers.
- Privilege profiles and permission overrides constrain Agent tool construction.
- Audit is redacted by construction.
- Docker isolation is available for dangerous local execution, with network and image choices controlled by trusted policy.

## Current and future scope

Implemented now:

- CLI and Desktop as first-class clients;
- local and trusted-LAN Node use;
- shared Config, Models, Extensions, Actions, Operations, and Runtime;
- long-task, automation, security, and observability foundations.

Future layers include Mobile, multi-target discovery, secure remote connectivity, public catalogs, cloud service operation, improved memory, self-evolution, and deeper long-task planning. These should extend the current Node boundary rather than introduce a competing backend.
