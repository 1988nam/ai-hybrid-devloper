# Local Coding Factory source

This directory is the Git-tracked source of truth for the Local Coding Factory used by AI Hybrid Developer.

The installed runtime lives separately, by default at:

`~/local-coding-factory/`

Tracked source:

- `factory.py` — CLI, project config loading, run creation/resume/status, Ollama tunnel setup.
- `runner.py` — per-Story worktrees, Aider developer loop, deterministic verification, independent reviewer, retry, checkpoint/recovery, and final verification.
- `guard_tests.py` — protects existing tests from deletion, assertion weakening, skip/todo insertion, and forced success.

Runtime-only data stays outside Git:

- `runs/`
- `inbox/`
- logs
- `current.json`
- machine-specific project TOMLs containing local paths, hostnames, usernames, or ports

Run:

```bash
bash scripts/install.sh
```

from the repository root to sync the tracked Python source into the runtime directory. The installer does not delete runtime state or project configuration.

## Story branch isolation

Story branches are namespaced by the integration run:

```text
local-agent/factory-olchangi-20260920-224206/S1
local-agent/factory-olchangi-20260920-231500/S1
```

This prevents a failed or abandoned run from leaving a `local-agent/S1` branch that blocks the next run.
