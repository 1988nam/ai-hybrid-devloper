# AI Hybrid Developer

A local browser Agent for a hybrid development workflow.

**Frontier models plan. Local models loop until the code is verified.**

The browser is the human control plane. Aider stays hidden inside the backend as an edit executor; you do not interact with it directly.

## Workflow

```text
Requirement
    ↓
OpenAI Codex (ChatGPT login)
or Gemini CLI (Google login)
    ↓
repository-aware DESIGN.md + stories JSON
    ↓
AI Hybrid Developer (browser)
    ↓
tracked Local Coding Factory source
(factory/*.py)
    ↓ install.sh
local runtime at ~/local-coding-factory
    ↓
Aider + local DEV model
    ↓
tests / build / custom gates
    ↓
independent local REVIEW model
    ↓
retry / commit / checkpoint / resume
```

## Frontier Planner

The Run screen can now generate the work package directly from a plain-language requirement.

### OpenAI Codex

- Uses the official Codex CLI already installed on the WSL host.
- The dashboard can start the official ChatGPT OAuth flow through `codex app-server`.
- The saved Codex login is reused by non-interactive `codex exec`.
- Planning runs in an explicit read-only Codex sandbox with approvals disabled.
- Codex structured output is constrained to the work-package schema.

### Gemini

- Uses the official Gemini CLI on the WSL host.
- The dashboard reuses the Gemini CLI's own cached authentication.
- For a first-time Google login, the Connect action opens a Gemini CLI terminal; choose **Sign in with Google** there once.
- Subsequent planning runs are non-interactive and use JSON output.
- Planning runs with Gemini CLI approval mode `plan`.

The dashboard never reads or copies OAuth token contents from either provider.

## What the dashboard does

- Reads projects from `~/local-coding-factory/projects/*.toml`.
- Ships the Local Coding Factory execution engine under `factory/` as Git-tracked source.
- Installs/synchronizes `factory.py`, `runner.py`, and `guard_tests.py` into the local runtime without deleting runs, inbox packages, logs, or project config.
- Lets a frontier planner inspect the configured project's current repository without editing it.
- Generates and fills editable Title, Design Markdown, and Stories JSON fields.
- Still accepts manually pasted or uploaded design/story artifacts.
- Archives every submitted work package.
- Compiles the shared design into each runtime story so local models receive the frontier-model decisions.
- Starts the existing `factory run` backend as a detached local process.
- Shows run status, Story progress, logs, and the integration diff.
- Supports Stop and Resume.
- Optionally opens the integration worktree in VS Code as an escape hatch.
- Uses only the Python standard library for the web/control-plane server.

## Install on WSL Ubuntu

```bash
git clone https://github.com/1988nam/ai-hybrid-devloper.git
cd ai-hybrid-devloper
bash scripts/install.sh
```

Start the local Agent:

```bash
ai-hybrid-developer --open
```

Then open:

```text
http://127.0.0.1:8787
```

The installer creates or refreshes the Local Factory runtime at `~/local-coding-factory` (or `FACTORY_HOME`) and installs both `factory` and `ai-hybrid-developer` launchers under `~/.local/bin`. Existing runtime state and machine-specific project TOMLs are preserved.

The frontier buttons appear even if a planner CLI is missing; the UI reports which optional CLI still needs to be installed.

## Work package format

The frontend stores frontier-model output as a package containing:

- `design.md` — shared architecture/design context.
- `stories.source.json` — the editable frontier story queue.
- `stories.json` — runtime stories with the design context compiled into each prompt.
- `package.toml` — package metadata.

A Story looks like:

```json
[
  {
    "id": "story-001",
    "title": "Add account switch guard",
    "prompt": "Implement the story-specific behavior...",
    "allowed_paths": [
      "shared/auth.js",
      "tests/auth.test.mjs"
    ]
  }
]
```

TOML remains the project/runtime configuration format under `~/local-coding-factory/projects/`.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile run.py app/*.py factory/*.py
bash -n scripts/install.sh
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component boundaries and state ownership.
