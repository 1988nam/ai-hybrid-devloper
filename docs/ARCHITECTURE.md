# Architecture

## Goal

AI Hybrid Developer is a local browser-based control plane for the already validated `local-coding-factory` backend. It deliberately does **not** reimplement the coding loop.

The system has two planes:

1. **Frontier planning plane** — expensive repository-aware reasoning that turns a requirement into design decisions and an executable Story queue.
2. **Local loop plane** — long-running implementation, deterministic verification, independent review, retry, commit, and resume.

## End-to-end flow

```text
Human requirement
   |
   +--> OpenAI Codex (ChatGPT OAuth)
   |         or
   +--> Gemini CLI (Google OAuth/cache)
             |
             v
       read-only repo analysis
             |
             v
       design + story queue
             |
             v
Browser Agent (127.0.0.1:8787)
             |
             v
      package archive/compiler
             |
             v
        factory CLI
             |
             v
          runner.py
        /            \
 Aider + DEV       REVIEW model
      |                 |
      +-- verify/retry --+
             |
        commit/resume
```

## Frontier planning plane

`app/planner.py` owns provider adapters and structured work-package generation.

### Codex adapter

- Detects the official `codex` CLI and existing login.
- Uses `codex app-server` account RPCs for browser-native ChatGPT OAuth and account metadata.
- Uses `codex exec` for non-interactive repository analysis.
- Uses an explicit read-only sandbox, no approvals, ephemeral execution, and an output JSON schema.
- Checks Git status before and after planning and rejects a planner run that changes repository state.

### Gemini adapter

- Detects the official `gemini` CLI and its configured auth mode.
- Does not extract or reuse Google OAuth token values itself.
- First-time authentication is delegated to the official interactive Gemini CLI.
- Subsequent planning uses non-interactive JSON output and `plan` approval mode.
- The same before/after Git status guard protects repository state.

### Planner output contract

Every provider is normalized to:

```json
{
  "title": "...",
  "design_markdown": "...",
  "stories": [
    {
      "id": "story-001",
      "title": "...",
      "prompt": "...",
      "allowed_paths": ["..."],
      "verify_commands": ["..."]
    }
  ]
}
```

The UI keeps generated output editable. The human can adjust the design or Story queue before pressing **개발 시작**.

### Local execution story budget

Frontier planning is optimized for the smaller local coding model rather than for the
fewest possible Stories.

- target: 2-4 editable files per Story
- hard maximum: 5 `allowed_paths` entries, including tests
- maximum: 2 new files in one Story
- `allowed_paths` is an edit-permission list, not read context
- abstraction introduction and mass consumer migration must be separate Stories
- VM/test-harness compatibility must precede migrations that depend on a new runtime/helper
- storage/config, diary generation, photos/Drive, auth/session, build, worker/API,
  portal/navigation, service worker, browser regression, and docs are separate concerns
- base `npm test` and `npm run build` are not repeated in `verify_commands`
- repo paths are literal strings; regex-style escaping such as `sw\\.js` is rejected

After the first frontier plan, the dashboard validates the Story queue deterministically.
If any Story violates the local budget, the same frontier provider is automatically called
again as a **Story Slicer**. The architecture title/design are locked; only Story
partitioning may change. At most two automatic re-slicing passes are attempted. A queue
that still violates the hard budget is rejected before Local Factory starts.

## Local loop plane

The existing coding factory remains authoritative for:

- per-Story worktrees
- Aider execution
- local developer model
- scope, diff, test, build, and custom verification
- independent local reviewer
- bounded fresh-process retry
- commit and ff-only integration
- checkpoint and crash resume

The dashboard never calls Aider directly.

## Browser control plane

The Python server uses the standard library and serves the static frontend plus a same-origin JSON API.

Factory endpoints:

- `GET /api/projects`
- `GET /api/projects/{name}/status`
- `GET /api/projects/{name}/logs`
- `GET /api/projects/{name}/diff`
- `POST /api/projects/{name}/runs`
- `POST /api/projects/{name}/resume`
- `POST /api/projects/{name}/stop`
- `POST /api/projects/{name}/open-vscode`

Planner endpoints:

- `GET /api/planners`
- `GET /api/planners/codex/login-status`
- `POST /api/planners/codex/connect`
- `POST /api/planners/codex/logout`
- `POST /api/planners/gemini/connect`
- `POST /api/projects/{name}/plan`

## State ownership

The dashboard owns control-plane state such as provider execution logs and the background factory process PID.

The coding factory remains the source of truth for implementation state:

- `~/local-coding-factory/projects/*.toml`
- `~/local-coding-factory/runs/<project>/current.json`
- each run's `runtime/state.json`

Planner logs live below the dashboard state directory, not inside the target project repository.

## Safety

- The server binds to `127.0.0.1` by default and has no remote authentication in v1.
- Frontier planning is explicitly read-only and guarded by Git status comparison.
- A project can have only one dashboard-launched active factory process at a time.
- Stop sends SIGTERM to the factory process group, followed by SIGKILL after a grace period.
- The factory's own lock/state/resume behavior is still authoritative if the web server restarts.
- The dashboard does not merge to `main`.
