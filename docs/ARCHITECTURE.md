# Architecture

## Goal

AI Hybrid Developer is a local browser-based control plane for the already validated `local-coding-factory` backend. It deliberately does **not** reimplement the coding loop.

The human-facing workflow is:

1. A frontier model produces a design document and machine-executable stories.
2. The user pastes or loads those artifacts in the local browser Agent.
3. The dashboard archives the package as `design.md`, `stories.source.json`, compiled `stories.json`, and `package.toml`.
4. Shared design context is prepended to every runtime story so the local developer/reviewer models receive the frontier-model architecture decisions.
5. The dashboard launches the existing `factory run <project> <stories.json>` command in a detached process group.
6. The existing factory remains authoritative for worktrees, Aider, developer model, deterministic verification, reviewer model, retry, commit, checkpoint, and resume.
7. The browser polls local state/log/diff endpoints and never needs to talk to Aider directly.

## Components

```text
Frontier model
  -> design.md + stories JSON
  -> Browser Agent (127.0.0.1:8787)
     -> package archive
     -> factory CLI
        -> runner.py
           -> Aider (edit executor)
           -> local developer model
           -> deterministic verification
           -> local reviewer model
           -> retry / commit / resume
```

## Browser control plane

The Python server intentionally uses only the standard library for v1. It serves the static frontend and a small same-origin JSON API.

Key endpoints:

- `GET /api/projects`
- `GET /api/projects/{name}/status`
- `GET /api/projects/{name}/logs`
- `GET /api/projects/{name}/diff`
- `POST /api/projects/{name}/runs`
- `POST /api/projects/{name}/resume`
- `POST /api/projects/{name}/stop`
- `POST /api/projects/{name}/open-vscode`

## State ownership

The dashboard owns only control-plane state such as the background process PID and its combined log path.

The coding factory remains the source of truth for run state:

- `~/local-coding-factory/projects/*.toml`
- `~/local-coding-factory/runs/<project>/current.json`
- each run's `runtime/state.json`

This separation keeps the UI replaceable and prevents the browser layer from becoming a second orchestration engine.

## Safety

- The server binds to `127.0.0.1` by default and has no remote authentication in v1.
- A project can have only one dashboard-launched active factory process at a time.
- Stop sends SIGTERM to the factory process group, followed by SIGKILL after a grace period.
- The factory's own lock/state/resume behavior is still authoritative if the web server restarts.
- The dashboard does not merge to `main`.
