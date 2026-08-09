# Node Operations

One long-lived OpenPPX Node owns the Client API, Runtime Supervisor, Task scheduler, Cron, Heartbeat, usage facts, and Action audit lifecycle.

## Start a Node

Foreground operation:

```bash
ppx node run --node-root ~/.openppx
```

The listener comes from `node.json` unless `--host` or `--port` is supplied as an explicit deployment override. The conventional local endpoint is `http://127.0.0.1:18765`.

Check the shared Operations projection from another terminal:

```bash
ppx operations status
ppx operations health
```

For a protected or remote Node:

```bash
ppx operations health \
  --url http://<node-address>:18765 \
  --token '<bearer-token>'
```

## User service

OpenPPX can write a launchd user manifest on macOS or a systemd user unit on Linux:

```bash
ppx node service install --node-root ~/.openppx
ppx node service status
```

The installer does not silently enable or start the service. Review the generated manifest, then use `launchctl` or `systemctl --user` explicitly. Logs are written below `<node-root>/logs/`.

## Unified status and health

`operations.status` reports Node identity, setup state, Runtime state, enabled Agents, extension readiness, and automation lifecycle. `operations.health` reports bounded component health without exposing paths or credentials.

```bash
ppx operations status --json
ppx operations health --json
```

The Desktop Operations screen consumes these exact Actions. It provides Task inspection and supported controls, Cron create/edit/enable/disable/run/remove, Heartbeat status and run-now, and the same usage/audit facts without maintaining a separate scheduler.

## Tasks

Persistent `TaskRun` facts represent supervised background work, subagents, Cron turns, remote jobs, and other long operations.

```bash
ppx operations tasks --limit 20
ppx operations tasks --session <session-id> --limit 50
```

Task state, events, artifacts, checkpoints, cancellation controls, and delivery facts remain durable below the Node root. A terminal state is based on runner evidence, not a model claim.

### Background Subagents

`spawn_subagent` runs a background task with the same business Agent, not a different Agent identity. Each spawn receives:

- a separate Google ADK Session;
- the exact Agent Config, permission, and Extension revisions trusted by the parent call;
- a restricted Tool catalog that removes recursive `spawn_subagent`;
- a durable `TaskRun` and Node-owned runtime Run;
- the parent route and original ADK function-call ID for result delivery.

The worker fails closed before starting if any captured revision is stale. If the permission revision changes while it is running, its next Tool Action is rejected instead of inheriting expanded authority. A parent Session can have at most four active Subagents, and retrying the same function call resolves to the same deterministic Task rather than launching a duplicate.

On completion, OpenPPX retains a bounded Task result and appends a native ADK `FunctionResponse` to the parent Session. The original conversation is not blocked while the worker runs. Task inspection, output, and cooperative cancellation are supported. Interrupt, pause, rejoin, and restart-resume are not advertised because this worker has no durable checkpoint boundary; a Node process loss makes an attached running worker non-resumable.

## Cron

Cron belongs to the Node process. Jobs execute only while Node automation is running.

List jobs and recent history:

```bash
ppx operations cron list --history-limit 20
```

Create one interval job:

```bash
ppx operations cron create \
  --name daily-review \
  --agent main \
  --message 'Review open tasks and summarize blockers.' \
  --every-seconds 86400 \
  --yes
```

Other schedules:

```bash
ppx operations cron create \
  --name weekday-review \
  --agent main \
  --message 'Review open tasks.' \
  --cron-expression '0 9 * * 1-5' \
  --timezone Asia/Shanghai \
  --yes

ppx operations cron create \
  --name one-time \
  --agent main \
  --message 'Prepare the release checklist.' \
  --at-ms <unix-time-ms> \
  --delete-after-run \
  --yes
```

Lifecycle controls:

```bash
ppx operations cron disable <job-id> --yes
ppx operations cron enable <job-id> --yes
ppx operations cron run <job-id> --force --yes
ppx operations cron remove <job-id> --yes
```

The stored job payload contains only message, Agent, and user identity. Execution and results appear as Node-owned Task and audit facts.

## Heartbeat

Heartbeat periodically asks the configured Agent to inspect current work and report only items that need operator attention. Its schedule and prompt come from `NodeConfig.spec.operations.heartbeat`.

```bash
ppx operations heartbeat status
ppx operations heartbeat run --reason manual
```

Heartbeat can be disabled, interval-bounded, and restricted to configured active hours. It skips work when the runtime reports that it is busy.

## Usage

```bash
ppx operations usage --limit 20
ppx operations usage --provider google --limit 100
```

Usage facts are operational observations. They do not include model credentials or message bodies.

## Action audit

```bash
ppx operations audit --limit 50
ppx operations audit --actor <principal-id>
ppx operations audit --agent main
ppx operations audit --action extension.install
ppx operations audit --outcome denied
```

Audit rows contain bounded Action identity, actor, policy decision, targets, timestamps, and outcome. Inputs, outputs, Secrets, prompts, and model text are deliberately excluded. High-risk mutations fail closed if the audit start record cannot be written; read-only queries remain fault tolerant.

## Logs and data

Current Node-owned locations:

```text
<node-root>/logs/node.out.log
<node-root>/logs/node.err.log
<node-root>/database/
<node-root>/artifacts/
<node-root>/memory/
```

The exact database layout is an implementation detail. Use Operations and Action projections rather than querying SQLite from clients.

## Troubleshooting

### Address already in use

```bash
lsof -nP -iTCP:18765 -sTCP:LISTEN
```

Keep exactly one process bound to a Node root and listener. Do not start a second Node merely to run another Agent; one Node can own multiple enabled Agents.

### Node needs configuration

```bash
ppx setup
```

If setup is intentionally incomplete, inspect the state with Desktop onboarding or `setup.status` through `ppx action invoke`.

### Node is configured but not ready

Run the first real Hello again after fixing model readiness:

```bash
ppx setup --provider <provider> --model <model-id>
```

### LAN request is unauthorized

Confirm that the Node process and client use the same bearer token. Tokens cannot contain whitespace, do not belong in URLs, and are never accepted as query parameters.

### Extension or model changes are not visible in an active Run

This is expected. Runs pin immutable Config and Extension snapshots. Start a new Run after the change; do not mutate a live runtime in place.

## Graceful shutdown

Interrupt the foreground Node with `Ctrl+C`. Shutdown stops accepting requests, stops Node-owned automation, closes extension connections, and then closes Runtime instances. The durable Session, Task, Config, Extension, audit, and usage facts reopen on the next Node start.
