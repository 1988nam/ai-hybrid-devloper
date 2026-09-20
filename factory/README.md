# Local Coding Factory source

This directory is the Git-tracked source of truth for the Local Coding Factory used by AI Hybrid Developer.

Runtime installation lives separately, by default at:

`~/local-coding-factory/`

Tracked source:
- `factory.py` — CLI, project config loading, run creation/resume/status, Ollama tunnel setup.
- `runner.py` — per-Story worktrees, Aider developer loop, deterministic verification, Qwen reviewer, retry, checkpoint/recovery, final verification.
- `guard_tests.py` — protects existing tests from deletion, assertion weakening, skip/todo insertion, and forced success.

Runtime-only data must stay outside Git:
- `runs/`
- `inbox/`
- logs
- `current.json`
- machine-specific project TOMLs containing local paths, hostnames, usernames, or ports.

The repository installer copies these tracked Python files into the runtime directory without deleting runtime state or project configuration.
