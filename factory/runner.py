#!/usr/bin/env python3

import argparse
import difflib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_VERIFY = json.loads(
    os.environ.get(
        "FACTORY_VERIFY_COMMANDS_JSON",
        '["npm test", "npm run build"]',
    )
)

MAX_DEV_ATTEMPTS = int(
    os.environ.get("FACTORY_MAX_DEV_ATTEMPTS", "3")
)

MAX_REVIEW_PROTOCOL_ATTEMPTS = int(
    os.environ.get(
        "FACTORY_MAX_REVIEW_PROTOCOL_ATTEMPTS",
        "2",
    )
)

DEV_TIMEOUT = int(
    os.environ.get("FACTORY_DEV_TIMEOUT", "1800")
)

VERIFY_TIMEOUT = int(
    os.environ.get("FACTORY_VERIFY_TIMEOUT", "900")
)

REVIEW_TIMEOUT = int(
    os.environ.get("FACTORY_REVIEW_TIMEOUT", "600")
)

DEV_MODEL = os.environ.get(
    "FACTORY_DEV_MODEL",
    "ollama_chat/qwen3.6:35b-a3b-coding",
)

REVIEW_MODEL = os.environ.get(
    "FACTORY_REVIEW_MODEL",
    "qwen3.8:27b",
)


def atomic_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")

    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    os.replace(tmp, path)


def tail_text(path: Path, max_chars=12000):
    if not path.exists():
        return ""

    text = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    return text[-max_chars:]


def stream_process(cmd, cwd, env, log_path, timeout):
    print("+", " ".join(map(str, cmd)), flush=True)

    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            list(map(str, cmd)),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def reader():
            assert proc.stdout is not None

            for line in proc.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()

        thread = threading.Thread(
            target=reader,
            daemon=True,
        )

        thread.start()

        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(
                f"\nPROCESS_TIMEOUT after {timeout}s",
                flush=True,
            )

            proc.kill()
            rc = 124

        thread.join(timeout=10)

        return rc


class Runner:
    def __init__(self, args):
        self.args = args

        self.base = Path(args.repo).resolve()
        self.stories_file = Path(args.stories).resolve()
        self.runtime = Path(args.runtime).resolve()
        self.worktree_root = Path(args.worktree_root).resolve()

        self.runtime.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.worktree_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.state_file = self.runtime / "state.json"

        self.ollama_base = args.ollama.rstrip("/")
        self.node_modules = (
            Path(args.node_modules).resolve()
            if args.node_modules
            else None
        )

        self.stories = json.loads(
            self.stories_file.read_text(encoding="utf-8")
        )

        self.lock_file = (
            self.runtime / "runner.lock"
        ).open("a+")

        try:
            fcntl.flock(
                self.lock_file,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            raise RuntimeError(
                "another runner instance is already active"
            )

    # ------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------

    def run(
        self,
        cmd,
        cwd=None,
        capture=False,
        timeout=None,
    ):
        if cwd is None:
            cwd = self.base

        print(
            "+",
            " ".join(map(str, cmd)),
            flush=True,
        )

        try:
            return subprocess.run(
                list(map(str, cmd)),
                cwd=cwd,
                text=True,
                stdout=(
                    subprocess.PIPE
                    if capture
                    else None
                ),
                stderr=(
                    subprocess.PIPE
                    if capture
                    else None
                ),
                timeout=timeout,
            )

        except subprocess.TimeoutExpired as e:
            print(
                f"COMMAND_TIMEOUT: {' '.join(map(str, cmd))}"
            )

            return subprocess.CompletedProcess(
                cmd,
                124,
                stdout=e.stdout or "",
                stderr=e.stderr or "",
            )

    def git(
        self,
        *args,
        cwd=None,
        capture=False,
    ):
        return self.run(
            ["git", *args],
            cwd=cwd or self.base,
            capture=capture,
        )

    def git_output(
        self,
        *args,
        cwd=None,
    ):
        p = self.git(
            *args,
            cwd=cwd,
            capture=True,
        )

        if p.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed\n"
                f"{p.stderr or p.stdout}"
            )

        return p.stdout.strip()

    # ------------------------------------------------------------
    # State
    # ------------------------------------------------------------

    def save_state(self, state):
        atomic_json(
            self.state_file,
            state,
        )

    def new_state(self):
        branch = self.git_output(
            "branch",
            "--show-current",
        )

        head = self.git_output(
            "rev-parse",
            "HEAD",
        )

        if branch != self.args.integration_branch:
            raise RuntimeError(
                f"expected integration branch "
                f"{self.args.integration_branch}, "
                f"found {branch}"
            )

        state = {
            "version": 1,
            "integration_branch": branch,
            "base_commit": head,
            "head": head,
            "next_index": 0,
            "completed": [],
            "inflight": None,
            "status": "running",
        }

        self.save_state(state)

        print(
            f"NEW_STATE base={head[:8]}"
        )

        return state

    def load_state(self):
        if not self.state_file.exists():
            return self.new_state()

        state = json.loads(
            self.state_file.read_text(
                encoding="utf-8"
            )
        )

        branch = self.git_output(
            "branch",
            "--show-current",
        )

        if (
            branch
            != state["integration_branch"]
            or branch
            != self.args.integration_branch
        ):
            raise RuntimeError(
                "state/integration branch mismatch"
            )

        print(
            "STATE_LOADED "
            f"next_index={state['next_index']} "
            f"status={state['status']}"
        )

        return state

    # ------------------------------------------------------------
    # Worktree helpers
    # ------------------------------------------------------------

    def safe_id(self, story_id):
        return re.sub(
            r"[^A-Za-z0-9._-]+",
            "-",
            story_id,
        )

    def branch_name(self, story):
        run_namespace = self.safe_id(
            self.args.integration_branch
        )

        return (
            "local-agent/"
            + run_namespace
            + "/"
            + self.safe_id(story["id"])
        )

    def worktree_path(self, story):
        return (
            self.worktree_root
            / self.safe_id(story["id"])
        )

    def cleanup_story(self, story):
        wt = self.worktree_path(story)
        branch = self.branch_name(story)

        subprocess.run(
            [
                "git",
                "worktree",
                "remove",
                "--force",
                str(wt),
            ],
            cwd=self.base,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        subprocess.run(
            [
                "git",
                "branch",
                "-D",
                branch,
            ],
            cwd=self.base,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def create_story_worktree(self, story):
        self.cleanup_story(story)

        wt = self.worktree_path(story)
        branch = self.branch_name(story)

        p = self.git(
            "worktree",
            "add",
            "-b",
            branch,
            str(wt),
            "HEAD",
        )

        if p.returncode != 0:
            raise RuntimeError(
                f"failed to create worktree for "
                f"{story['id']}"
            )

        if (
            self.node_modules
            and self.node_modules.exists()
        ):
            target = wt / "node_modules"

            if target.exists() or target.is_symlink():
                if (
                    target.is_dir()
                    and not target.is_symlink()
                ):
                    shutil.rmtree(target)
                else:
                    target.unlink()

            target.symlink_to(
                self.node_modules,
                target_is_directory=True,
            )

        return wt

    # ------------------------------------------------------------
    # Change/scope handling
    # ------------------------------------------------------------

    def cleanup_aider_artifacts(self, wt):
        for path in wt.glob(
            ".aider.tags.cache.*"
        ):
            if path.is_dir():
                shutil.rmtree(
                    path,
                    ignore_errors=True,
                )
            else:
                path.unlink(
                    missing_ok=True
                )

        (wt / ".aider.input.history").unlink(
            missing_ok=True
        )

    def changed_files(self, wt):
        tracked = self.git(
            "diff",
            "--name-only",
            "HEAD",
            cwd=wt,
            capture=True,
        )

        untracked = self.git(
            "ls-files",
            "--others",
            "--exclude-standard",
            cwd=wt,
            capture=True,
        )

        names = set()

        if tracked.returncode == 0:
            names.update(
                line.strip()
                for line
                in tracked.stdout.splitlines()
                if line.strip()
            )

        if untracked.returncode == 0:
            names.update(
                line.strip()
                for line
                in untracked.stdout.splitlines()
                if line.strip()
            )

        names.discard("node_modules")

        return sorted(names)

    # ------------------------------------------------------------
    # Developer
    # ------------------------------------------------------------

    def developer_prompt(
        self,
        story,
        feedback="",
    ):
        prompt = f"""
Implement exactly one story.

Story ID:
{story['id']}

Title:
{story.get('title', story['id'])}

Requirements:
{story['prompt']}

Allowed paths:
{json.dumps(story['allowed_paths'], indent=2)}

Rules:
- Work only on this story.
- Modify only allowed paths.
- Do not weaken, delete or bypass tests.
- Do not commit.
- The outer runner owns verification, review, commits and merges.
- Make actual file changes, not just an explanation.
"""

        if feedback:
            prompt += f"""

A PREVIOUS FRESH ATTEMPT WAS REJECTED.

The following verifier/reviewer feedback is authoritative debugging context:

--- BEGIN FEEDBACK ---
{feedback}
--- END FEEDBACK ---

Inspect the CURRENT files on disk.

Address every blocking issue.
Do not weaken tests to make failures disappear.
If the reviewer identifies a missing regression test,
add a focused test that would have caught the defect.
Make the smallest correct change.
"""

        return prompt

    def run_developer(
        self,
        story,
        wt,
        attempt,
        feedback,
    ):
        story_id = story["id"]

        prompt_path = (
            self.runtime
            / f"{story_id}-dev-{attempt}.prompt.txt"
        )

        prompt_path.write_text(
            self.developer_prompt(
                story,
                feedback,
            ),
            encoding="utf-8",
        )

        cmd = ["aider"]

        for rel in story["allowed_paths"]:
            path = wt / rel

            if path.exists() and path.is_file():
                cmd.append(rel)

        cmd += [
            "--model",
            DEV_MODEL,
            "--edit-format",
            "diff",
            "--map-tokens",
            "2048",
            "--no-auto-commits",
            "--no-dirty-commits",
            "--no-gitignore",
            "--no-restore-chat-history",
            "--input-history-file",
            str(
                self.runtime
                / f"{story_id}-dev-{attempt}.input.txt"
            ),
            "--chat-history-file",
            str(
                self.runtime
                / f"{story_id}-dev-{attempt}.chat.md"
            ),
            "--llm-history-file",
            str(
                self.runtime
                / f"{story_id}-dev-{attempt}.llm.md"
            ),
            "--yes-always",
            "--no-check-update",
            "--message-file",
            str(prompt_path),
        ]

        env = os.environ.copy()
        env["OLLAMA_API_BASE"] = (
            self.ollama_base
        )

        log = (
            self.runtime
            / f"{story_id}-dev-{attempt}.log"
        )

        rc = stream_process(
            cmd,
            wt,
            env,
            log,
            DEV_TIMEOUT,
        )

        self.cleanup_aider_artifacts(wt)

        subprocess.run(
            ["git", "reset"],
            cwd=wt,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return rc, log

    # ------------------------------------------------------------
    # Deterministic verification
    # ------------------------------------------------------------

    def run_shell_check(
        self,
        command,
        wt,
        log,
    ):
        rendered = (
            command
            .replace(
                "{repo}",
                str(wt),
            )
        )

        p = subprocess.run(
            [
                "bash",
                "-lc",
                rendered,
            ],
            cwd=wt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=VERIFY_TIMEOUT,
        )

        with log.open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                f"\n--- COMMAND ---\n{rendered}\n"
            )
            f.write(
                f"--- EXIT={p.returncode} ---\n"
            )
            f.write(p.stdout)

        print(p.stdout, end="")

        return p.returncode

    def verify_story(
        self,
        story,
        wt,
        attempt,
    ):
        log = (
            self.runtime
            / f"{story['id']}-verify-{attempt}.log"
        )

        log.write_text(
            "",
            encoding="utf-8",
        )

        files = self.changed_files(wt)

        allowed = set(
            story["allowed_paths"]
        )

        forbidden = [
            path
            for path in files
            if path not in allowed
        ]

        results = []

        if forbidden:
            message = (
                "SCOPE_GATE_FAIL:\n"
                + "\n".join(forbidden)
                + "\n"
            )

            print(message)

            with log.open(
                "a",
                encoding="utf-8",
            ) as f:
                f.write(message)

            results.append(
                ("scope", 70)
            )
        else:
            print(
                "SCOPE_GATE_OK:",
                ", ".join(files),
            )

            results.append(
                ("scope", 0)
            )

        p = self.git(
            "diff",
            "--check",
            cwd=wt,
            capture=True,
        )

        with log.open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                "\n--- git diff --check ---\n"
            )
            f.write(
                p.stdout or ""
            )
            f.write(
                p.stderr or ""
            )

        results.append(
            ("diff-check", p.returncode)
        )

        # Protect the pre-existing test suite before normal verification.
        guard_script = Path(__file__).with_name("guard_tests.py")

        if (
            os.environ.get("FACTORY_TEST_GUARD", "1") != "0"
            and guard_script.exists()
        ):
            try:
                guard_proc = subprocess.run(
                    [
                        sys.executable,
                        str(guard_script),
                        "--repo",
                        str(wt),
                    ],
                    cwd=wt,
                    env=os.environ.copy(),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=VERIFY_TIMEOUT,
                )

                print(guard_proc.stdout, end="")

                with log.open(
                    "a",
                    encoding="utf-8",
                ) as f:
                    f.write(
                        "\n--- existing-test guard "
                        f"exit={guard_proc.returncode} ---\n"
                    )
                    f.write(guard_proc.stdout)

                results.append(
                    ("existing-test-guard", guard_proc.returncode)
                )

            except subprocess.TimeoutExpired:
                results.append(
                    ("existing-test-guard", 124)
                )

        commands = list(DEFAULT_VERIFY)

        commands.extend(
            story.get(
                "verify_commands",
                [],
            )
        )

        for command in commands:
            rendered = (
                command
                .replace(
                    "{story_id}",
                    story["id"],
                )
                .replace(
                    "{repo}",
                    str(wt),
                )
            )

            try:
                rc = self.run_shell_check(
                    rendered,
                    wt,
                    log,
                )
            except subprocess.TimeoutExpired:
                rc = 124

                with log.open(
                    "a",
                    encoding="utf-8",
                ) as f:
                    f.write(
                        f"\nVERIFY_TIMEOUT: {rendered}\n"
                    )

            results.append(
                (rendered, rc)
            )

        for name, code in results:
            print(
                f"VERIFY {name}: {code}"
            )

        ok = all(
            code == 0
            for _, code in results
        )

        return ok, log, results

    # ------------------------------------------------------------
    # Reviewer input
    # ------------------------------------------------------------

    def candidate_diff(
        self,
        story,
        wt,
    ):
        allowed = story["allowed_paths"]

        p = self.git(
            "diff",
            "HEAD",
            "--",
            *allowed,
            cwd=wt,
            capture=True,
        )

        text = p.stdout or ""

        tracked = set(
            self.git_output(
                "ls-files",
                cwd=wt,
            ).splitlines()
        )

        for rel in allowed:
            path = wt / rel

            if (
                rel not in tracked
                and path.exists()
                and path.is_file()
            ):
                content = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )

                text += "\n" + "".join(
                    difflib.unified_diff(
                        [],
                        content.splitlines(True),
                        fromfile="/dev/null",
                        tofile=rel,
                    )
                )

        return text

    def review_prompt(
        self,
        story,
        wt,
    ):
        diff = self.candidate_diff(
            story,
            wt,
        )

        file_sections = []

        for rel in story["allowed_paths"]:
            path = wt / rel

            if path.exists() and path.is_file():
                content = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )

                if len(content) > 30000:
                    content = (
                        content[:30000]
                        + "\n...TRUNCATED...\n"
                    )

                file_sections.append(
                    f"FILE: {rel}\n"
                    f"--- BEGIN FILE ---\n"
                    f"{content}\n"
                    f"--- END FILE ---"
                )

            else:
                file_sections.append(
                    f"FILE: {rel}\n<DELETED OR ABSENT>"
                )

        if len(diff) > 50000:
            diff = (
                diff[:50000]
                + "\n...DIFF TRUNCATED...\n"
            )

        return f"""
Review this candidate implementation as an independent
read-only code reviewer.

STORY ID:
{story['id']}

REQUIREMENTS:
{story['prompt']}

ALLOWED PATHS:
{json.dumps(story['allowed_paths'], indent=2)}

The deterministic outer verifier has already passed:
- scope gate
- git diff --check
- npm test
- npm run build
- any story-specific verification commands

These are evidence, not proof of semantic correctness.

CANDIDATE DIFF:
--- BEGIN DIFF ---
{diff}
--- END DIFF ---

FINAL RELEVANT FILES:

{chr(10).join(file_sections)}

Return exactly one JSON object:

{{
  "verdict": "PASS" or "FAIL",
  "summary": "short explanation",
  "findings": [
    {{
      "severity": "blocking" or "warning",
      "file": "path or null",
      "message": "specific finding"
    }}
  ]
}}

Rules:
- FAIL if a story requirement is materially violated.
- FAIL for a concrete correctness or regression issue.
- Check whether tests meaningfully cover the new behavior.
- Identify suspicious test weakening.
- Do not invent missing repository context.
- Do not fail merely because you prefer a different style.
- A correct implementation with adequate tests should PASS.
"""

    # ------------------------------------------------------------
    # Reviewer
    # ------------------------------------------------------------

    def call_reviewer(
        self,
        story,
        wt,
        review_attempt,
    ):
        prompt = self.review_prompt(
            story,
            wt,
        )

        print(
            "Calling Qwen3.8 reviewer... "
            f"prompt_chars={len(prompt)}",
            flush=True,
        )

        payload = {
            "model": REVIEW_MODEL,
            "stream": False,
            "think": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "num_predict": 2048,
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an independent read-only "
                        "code reviewer. You cannot edit files. "
                        "Do not trust developer claims or "
                        "passing tests. Review semantics "
                        "against the supplied story. "
                        "Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }

        req = urllib.request.Request(
            self.ollama_base + "/api/chat",
            data=json.dumps(payload).encode(
                "utf-8"
            ),
            headers={
                "Content-Type":
                "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req,
                timeout=REVIEW_TIMEOUT,
            ) as response:
                raw = response.read().decode(
                    "utf-8"
                )

        except Exception as error:
            return (
                "PROTOCOL_ERROR",
                None,
                f"review request failed: {error}",
            )

        raw_path = (
            self.runtime
            / (
                f"{story['id']}"
                f"-review-{review_attempt}.raw.json"
            )
        )

        raw_path.write_text(
            raw,
            encoding="utf-8",
        )

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as error:
            return (
                "PROTOCOL_ERROR",
                None,
                f"Ollama envelope JSON invalid: {error}",
            )

        message = body.get(
            "message",
            {},
        )

        content = message.get(
            "content",
            "",
        ).strip()

        if not content:
            return (
                "PROTOCOL_ERROR",
                None,
                (
                    "empty reviewer message.content; "
                    f"done_reason={body.get('done_reason')}; "
                    f"eval_count={body.get('eval_count')}; "
                    f"thinking_chars="
                    f"{len(message.get('thinking', ''))}"
                ),
            )

        if content.startswith("```"):
            content = re.sub(
                r"^```(?:json)?\s*",
                "",
                content,
            )

            content = re.sub(
                r"\s*```$",
                "",
                content,
            )

        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            return (
                "PROTOCOL_ERROR",
                None,
                (
                    f"review content invalid JSON: "
                    f"{error}\n"
                    f"content={content[:2000]}"
                ),
            )

        verdict = str(
            result.get("verdict", "")
        ).upper()

        if verdict not in (
            "PASS",
            "FAIL",
        ):
            return (
                "PROTOCOL_ERROR",
                result,
                "review verdict must be PASS or FAIL",
            )

        review_file = (
            self.runtime
            / (
                f"{story['id']}"
                f"-review-{review_attempt}.json"
            )
        )

        atomic_json(
            review_file,
            result,
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        return (
            verdict,
            result,
            "",
        )

    def review_story(
        self,
        story,
        wt,
        dev_attempt,
    ):
        errors = []

        for protocol_attempt in range(
            1,
            MAX_REVIEW_PROTOCOL_ATTEMPTS + 1,
        ):
            result_type, review, error = (
                self.call_reviewer(
                    story,
                    wt,
                    (
                        f"dev{dev_attempt}"
                        f"-protocol{protocol_attempt}"
                    ),
                )
            )

            if result_type == "PASS":
                return (
                    "PASS",
                    review,
                    "",
                )

            if result_type == "FAIL":
                return (
                    "FAIL",
                    review,
                    "",
                )

            errors.append(error)

            print(
                "REVIEW_PROTOCOL_ERROR:",
                error,
            )

        return (
            "PROTOCOL_ERROR",
            None,
            "\n".join(errors),
        )

    # ------------------------------------------------------------
    # Story completion
    # ------------------------------------------------------------

    def commit_story(
        self,
        state,
        story,
        wt,
        index,
    ):
        allowed = story["allowed_paths"]

        p = self.git(
            "add",
            "-A",
            "--",
            *allowed,
            cwd=wt,
        )

        if p.returncode != 0:
            raise RuntimeError(
                "git add failed"
            )

        staged = self.git_output(
            "diff",
            "--cached",
            "--name-only",
            cwd=wt,
        ).splitlines()

        forbidden = [
            path
            for path in staged
            if path not in set(allowed)
        ]

        if forbidden:
            raise RuntimeError(
                "forbidden staged files: "
                + ", ".join(forbidden)
            )

        quiet = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--quiet",
            ],
            cwd=wt,
        )

        branch = self.branch_name(story)

        if quiet.returncode == 0:
            story_commit = self.git_output(
                "rev-parse",
                "HEAD",
                cwd=wt,
            )

            noop = True

        else:
            commit = self.git(
                "commit",
                "-m",
                f"agent: complete {story['id']}",
                cwd=wt,
            )

            if commit.returncode != 0:
                raise RuntimeError(
                    "story commit failed"
                )

            story_commit = self.git_output(
                "rev-parse",
                "HEAD",
                cwd=wt,
            )

            noop = False

        # Save recoverable state BEFORE merge.
        state["inflight"] = {
            "id": story["id"],
            "index": index,
            "phase": "committed",
            "story_commit": story_commit,
            "branch": branch,
            "noop": noop,
        }

        self.save_state(state)

        if not noop:
            merge = self.git(
                "merge",
                "--ff-only",
                branch,
                cwd=self.base,
            )

            if merge.returncode != 0:
                state["status"] = "merge_failed"
                self.save_state(state)

                raise RuntimeError(
                    "ff-only merge failed"
                )

        new_head = self.git_output(
            "rev-parse",
            "HEAD",
            cwd=self.base,
        )

        state["completed"].append({
            "id": story["id"],
            "commit": new_head,
            "noop": noop,
        })

        state["next_index"] = index + 1
        state["head"] = new_head
        state["inflight"] = None
        state["status"] = "running"

        self.save_state(state)

        self.cleanup_story(story)

        print(
            f"CHECKPOINT_SAVED "
            f"{story['id']} "
            f"next_index={state['next_index']} "
            f"head={new_head[:8]}"
        )

    # ------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------

    def recover_inflight(
        self,
        state,
    ):
        actual = self.git_output(
            "rev-parse",
            "HEAD",
        )

        inflight = state.get(
            "inflight"
        )

        if not inflight:
            if actual != state["head"]:
                raise RuntimeError(
                    "RESUME SAFETY STOP:\n"
                    f"state head={state['head']}\n"
                    f"actual head={actual}"
                )

            return state

        story = self.stories[
            inflight["index"]
        ]

        phase = inflight["phase"]

        if phase == "committed":
            story_commit = inflight[
                "story_commit"
            ]

            if actual == state["head"]:
                if not inflight.get(
                    "noop",
                    False,
                ):
                    merge = self.git(
                        "merge",
                        "--ff-only",
                        inflight["branch"],
                    )

                    if merge.returncode != 0:
                        raise RuntimeError(
                            "failed recovering "
                            "committed story merge"
                        )

                    actual = self.git_output(
                        "rev-parse",
                        "HEAD",
                    )

            elif actual == story_commit:
                # Crash occurred after merge but
                # before checkpoint update.
                pass

            else:
                raise RuntimeError(
                    "RESUME SAFETY STOP: "
                    "unexpected HEAD during "
                    "committed-story recovery"
                )

            if actual != story_commit:
                raise RuntimeError(
                    "recovered HEAD does not "
                    "match story commit"
                )

            if not any(
                item["id"] == story["id"]
                for item in state["completed"]
            ):
                state["completed"].append({
                    "id": story["id"],
                    "commit": actual,
                    "noop": inflight.get(
                        "noop",
                        False,
                    ),
                })

            state["next_index"] = (
                inflight["index"] + 1
            )

            state["head"] = actual
            state["inflight"] = None
            state["status"] = "running"

            self.save_state(state)
            self.cleanup_story(story)

            print(
                "RECOVERED_COMMITTED_STORY:",
                story["id"],
            )

            return state

        # Developing/reviewing attempt was interrupted.
        # Safe behavior is to discard that worktree
        # and restart the current story fresh.
        if actual != state["head"]:
            raise RuntimeError(
                "RESUME SAFETY STOP: "
                "integration HEAD changed while "
                "story was incomplete"
            )

        self.cleanup_story(story)

        state["inflight"] = None
        state["status"] = "running"

        self.save_state(state)

        print(
            "RECOVERED_INCOMPLETE_STORY:"
            f" restarting {story['id']}"
        )

        return state

    # ------------------------------------------------------------
    # Main story loop
    # ------------------------------------------------------------

    def process_story(
        self,
        state,
        story,
        index,
    ):
        print()
        print("=" * 72)
        print(
            f" STORY {story['id']}: "
            f"{story.get('title', '')}"
        )
        print("=" * 72)

        wt = self.create_story_worktree(
            story
        )

        state["inflight"] = {
            "id": story["id"],
            "index": index,
            "phase": "developing",
        }

        state["status"] = "running"

        self.save_state(state)

        feedback = ""

        for attempt in range(
            1,
            MAX_DEV_ATTEMPTS + 1,
        ):
            print()
            print(
                f"--- DEV ATTEMPT "
                f"{attempt}/{MAX_DEV_ATTEMPTS} ---"
            )

            dev_exit, dev_log = (
                self.run_developer(
                    story,
                    wt,
                    attempt,
                    feedback,
                )
            )

            if dev_exit != 0:
                feedback = (
                    "DEVELOPER PROCESS FAILED.\n\n"
                    + tail_text(dev_log)
                )

                print(
                    f"DEV_ERROR exit={dev_exit}"
                )

                continue

            verified, verify_log, _ = (
                self.verify_story(
                    story,
                    wt,
                    attempt,
                )
            )

            if not verified:
                feedback = (
                    "OUTER DETERMINISTIC "
                    "VERIFIER FAILED.\n\n"
                    + tail_text(
                        verify_log,
                        16000,
                    )
                )

                print(
                    "VERIFY_FAIL -> "
                    "fresh developer retry"
                )

                continue

            state["inflight"]["phase"] = (
                "reviewing"
            )

            self.save_state(state)

            review_type, review, error = (
                self.review_story(
                    story,
                    wt,
                    attempt,
                )
            )

            if review_type == "PROTOCOL_ERROR":
                state["status"] = (
                    "review_protocol_error"
                )

                self.save_state(state)

                print(
                    "REVIEW_PROTOCOL_ERROR "
                    "after retries:"
                )
                print(error)

                return False, 30

            if review_type == "FAIL":
                feedback = (
                    "INDEPENDENT REVIEWER "
                    "REJECTED THE IMPLEMENTATION.\n\n"
                    + json.dumps(
                        review,
                        indent=2,
                        ensure_ascii=False,
                    )
                )

                state["inflight"]["phase"] = (
                    "developing"
                )

                self.save_state(state)

                print(
                    "REVIEW_FAIL -> "
                    "fresh developer retry"
                )

                continue

            print(
                "REVIEW_PASS"
            )

            self.commit_story(
                state,
                story,
                wt,
                index,
            )

            return True, 0

        state["status"] = "failed"
        self.save_state(state)

        print(
            f"STORY_FAILED after "
            f"{MAX_DEV_ATTEMPTS} "
            f"developer attempts"
        )

        print(
            f"worktree retained: {wt}"
        )

        return False, 10

    # ------------------------------------------------------------
    # Final integration verify
    # ------------------------------------------------------------

    def final_verify(self):
        print()
        print("=" * 72)
        print(" FINAL INTEGRATION VERIFY")
        print("=" * 72)

        commands = list(DEFAULT_VERIFY)

        for story in self.stories:
            for cmd in story.get(
                "verify_commands",
                [],
            ):
                commands.append(
                    cmd.replace(
                        "{story_id}",
                        story["id"],
                    ).replace(
                        "{repo}",
                        str(self.base),
                    )
                )

        seen = set()

        for command in commands:
            if command in seen:
                continue

            seen.add(command)

            try:
                p = subprocess.run(
                    [
                        "bash",
                        "-lc",
                        command,
                    ],
                    cwd=self.base,
                    timeout=VERIFY_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                return False

            if p.returncode != 0:
                print(
                    "FINAL_VERIFY_FAIL:",
                    command,
                )
                return False

        return True

    # ------------------------------------------------------------
    # Run
    # ------------------------------------------------------------

    def execute(self):
        branch = self.git_output(
            "branch",
            "--show-current",
        )

        if branch != self.args.integration_branch:
            raise RuntimeError(
                f"wrong integration branch: {branch}"
            )

        state = self.load_state()

        state = self.recover_inflight(
            state
        )

        for index in range(
            state["next_index"]
        ):
            print(
                "RESUME_SKIP "
                f"{self.stories[index]['id']} "
                "(already completed)"
            )

        for index in range(
            state["next_index"],
            len(self.stories),
        ):
            story = self.stories[index]

            print(
                f"RUN_STORY "
                f"{story['id']} "
                f"index={index}"
            )

            ok, rc = self.process_story(
                state,
                story,
                index,
            )

            if not ok:
                return rc

            if (
                self.args.crash_after_story
                == story["id"]
            ):
                print(
                    "SIMULATED_HARD_CRASH "
                    f"after {story['id']}",
                    flush=True,
                )

                os._exit(99)

        if not self.final_verify():
            state["status"] = (
                "failed_final_verify"
            )

            self.save_state(state)

            return 20

        state["status"] = "complete"
        state["head"] = self.git_output(
            "rev-parse",
            "HEAD",
        )

        self.save_state(state)

        print()
        print("=" * 72)
        print(" LOCAL CODING FACTORY: PASS")
        print("=" * 72)

        return 0


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--repo",
        required=True,
    )

    parser.add_argument(
        "--stories",
        required=True,
    )

    parser.add_argument(
        "--integration-branch",
        required=True,
    )

    parser.add_argument(
        "--runtime",
        required=True,
    )

    parser.add_argument(
        "--worktree-root",
        required=True,
    )

    parser.add_argument(
        "--node-modules",
    )

    parser.add_argument(
        "--ollama",
        default=(
            "http://127.0.0.1:11436"
        ),
    )

    parser.add_argument(
        "--crash-after-story",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    try:
        runner = Runner(args)
        return runner.execute()

    except KeyboardInterrupt:
        print("\nINTERRUPTED")
        return 130

    except Exception as error:
        print()
        print(
            "RUNNER_FATAL:",
            repr(error),
        )
        return 40


if __name__ == "__main__":
    sys.exit(main())
