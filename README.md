# ppx-client

Local-first desktop client for openppx.

## Current Scope

This first version implements the local desktop workbench direction from the design docs:

- local runtime status card
- agent list
- session list
- chat workspace
- rich message rendering for common assistant content

## Architecture

```text
Renderer (React)
  -> preload host API
  -> Electron main
  -> local adapter seam
```

The current repository includes a local mock adapter so the desktop workflow is runnable before the real openppx local HTTP/SSE coordinator is connected.

## Development

```bash
pnpm install
pnpm dev
```

## Notes

- `task_plan.md`, `findings.md`, and `progress.md` are local planning artifacts and should not be committed.
- The next integration step is replacing the mock adapter with a real openppx local client-api service.
