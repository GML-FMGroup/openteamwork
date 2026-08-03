# Tool Guidance

OpenPPX supplies tool names, descriptions, and parameter schemas through ADK
function calling. This file records usage patterns and safety boundaries; it is
not a second copy of the runtime schema.

## Files and commands

- Use `read_file`, `list_dir`, `glob`, and `grep` to inspect before editing.
- Use `write_file` for a complete file and `edit_file` for a targeted change.
- `exec_command` supports foreground, background, PTY, timeout, scope, and
  sandbox options. Prefer the least-privileged execution mode that can finish
  the task.
- Commands and paths are checked against the active security policy. A denied
  operation is a boundary to explain, not something to bypass.
- Destructive or high-risk commands may require explicit confirmation.

## Skills and extension APIs

- Call `list_skills`, then `read_skill`, before relying on a Skill's workflow.
- `invoke_skill_api` executes APIs declared by a Skill. A fast call may return
  inline; a longer call returns a durable `task_id`.
- `list_skill_api_runners` shows the declarative API recipe types supported by
  this installation.
- Additional MCP, Plugin, and provider tools come from the immutable extension
  snapshot selected by the Node for the current request.

## Durable tasks

- Inspect work with `list_tasks`, `show_task`, `task_control_snapshot`,
  `task_runtime_status`, and `task_output`.
- Use `pause_task`, `resume_task`, `interrupt_task`, `cancel_task`, or
  `restart_task` only when their reported task capabilities permit it.
- Prefer an existing `task_id` over launching duplicate work.
- Maintenance cleanup tools default to a dry run and require confirmation for
  destructive changes.

## Planning and context

- `long_task` keeps the current objective visible across turns; it is not proof
  that execution completed.
- `write_todos` is suitable for a short ordered checklist.
- Task-flow tools model dependent steps and can synchronize durable task IDs.
- Context summary tools preserve bounded, inspectable state for long sessions.

## Web and browser

- `web_search` discovers sources; `web_fetch` extracts a known URL.
- The `browser` tool controls the configured host, Node, or sandbox browser
  target. Inspect status and tabs before mutating browser state.
- Network and private-address access remain subject to the active security
  policy and Node configuration.

## Scheduled Actions

Use the `cron` function tool rather than invoking a CLI command:

- `action="add"` requires a clear instruction in `message` and exactly one of
  `every_seconds`, `cron_expr`, or an absolute ISO datetime in `at`.
- `cron_expr` may include an IANA timezone through `tz`.
- `action="list"` returns persisted jobs.
- `action="remove"` requires `job_id` and confirmation.

The Node owns scheduling and persistence. Writing a reminder into a workspace
file does not schedule it.

## Delegation and GUI

`spawn_subagent` and GUI/computer tools appear only when enabled by the active
security and capability snapshot. Check the actual tool catalog instead of
assuming they are available.
