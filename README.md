# AI Hybrid Developer

A local browser Agent for the hybrid development workflow you already built.

**Frontier models plan. Local models loop until the code is verified.**

The browser UI is the human control plane. Aider stays hidden inside the backend as an edit executor; you do not interact with it directly.

## Workflow

```text
ChatGPT / Claude / Codex
        ↓
  DESIGN.md + stories JSON
        ↓
AI Hybrid Developer (browser)
        ↓
existing local-coding-factory
        ↓
Aider + local DEV model
        ↓
tests / build / custom gates
        ↓
independent local REVIEW model
        ↓
retry / commit / checkpoint / resume
```

## What v1 does

- Reads projects from `~/local-coding-factory/projects/*.toml`.
- Accepts a Markdown design specification and a JSON story queue.
- Archives every submitted work package.
- Compiles the shared design into each runtime story so local models receive the frontier-model decisions.
- Starts the existing `factory run` backend as a detached local process.
- Shows run status, Story progress, logs, and the integration diff.
- Supports Stop and Resume.
- Optionally opens the integration worktree in VS Code as an escape hatch.
- Uses only the Python standard library for the web server.

## Install on WSL Ubuntu

```bash
git clone https://github.com/1988nam/ai-hybrid-devloper.git
cd ai-hybrid-devloper
git switch feat/dashboard-v1
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

The dashboard expects the already-installed coding factory at `~/local-coding-factory` and the `factory` command in `PATH`.

## Work package format

The frontend accepts two frontier-model artifacts.

### `design.md`

Human/LLM architecture context: goals, constraints, decisions, non-goals, acceptance criteria, and shared design rules.

### Stories JSON

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
python3 -m py_compile run.py app/*.py
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component boundaries and state ownership.
