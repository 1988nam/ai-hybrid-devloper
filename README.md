# AI Hybrid Developer

Local browser-based control plane for a hybrid coding workflow:

- Frontier models design the work and produce a design document + story plan.
- A local coding factory executes the stories with isolated worktrees.
- Aider + a local developer model performs code changes.
- Deterministic verification runs tests/builds/gates.
- A separate local reviewer model performs semantic review.
- The browser dashboard is the human control plane: start, stop, resume, inspect logs, status and diffs.

The repository is being bootstrapped with the first dashboard implementation on a feature branch.
