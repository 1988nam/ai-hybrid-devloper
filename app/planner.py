from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class PlannerError(RuntimeError):
    """Expected, user-facing frontier planner error."""


CODEX_MODEL_OPTIONS = [
    {"id": "gpt-6-astra", "label": "GPT-6 Astra"},
    {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"},
    {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
    {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna"},
    {"id": "gpt-5.3-codex-spark", "label": "GPT-5.3 Codex Spark"},
]

CODEX_REASONING_EFFORT_OPTIONS = [
    {"id": "", "label": "Codex default"},
    {"id": "minimal", "label": "Minimal"},
    {"id": "low", "label": "Low"},
    {"id": "medium", "label": "Medium"},
    {"id": "high", "label": "High"},
    {"id": "xhigh", "label": "Extra High"},
]


LOCAL_STORY_TARGET_MAX_PATHS = 4
LOCAL_STORY_HARD_MAX_PATHS = 5
LOCAL_STORY_MAX_NEW_FILES = 2
LOCAL_STORY_MAX_RESLICE_PASSES = 2


PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "design_markdown": {"type": "string"},
        "stories": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "prompt": {"type": "string"},
                    "allowed_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "verify_commands": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "id",
                    "title",
                    "prompt",
                    "allowed_paths",
                    "verify_commands",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "design_markdown", "stories"],
    "additionalProperties": False,
}


def _is_windows_mount_path(path: str) -> bool:
    return bool(re.match(r"^/mnt/[A-Za-z]/", path))


def find_native_executable(name: str) -> str | None:
    """Prefer a Linux executable when the dashboard runs inside WSL."""
    first = shutil.which(name)

    if not os.environ.get("WSL_DISTRO_NAME"):
        return first

    candidates: list[str] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            candidates.append(str(candidate))

    if first and first not in candidates:
        candidates.insert(0, first)

    for candidate in candidates:
        if not _is_windows_mount_path(candidate):
            return candidate

    return None


def build_gemini_login_script(executable: str) -> str:
    bin_dir = str(Path(executable).parent)
    quoted_bin = shlex.quote(bin_dir)
    quoted_executable = shlex.quote(executable)

    return f"""#!/usr/bin/env bash
set +e

export PATH={quoted_bin}:"$PATH"

echo "========================================"
echo " AI Hybrid Developer / Gemini Login"
echo "========================================"
echo
echo "Gemini: {executable}"
echo "Node  : $(command -v node || echo NOT_FOUND)"
echo

{quoted_executable}
status=$?

echo
echo "Gemini CLI exited with code $status"
if [ "$status" -ne 0 ]; then
  echo
  echo "The login window is being kept open so you can read the error."
fi
echo
read -r -p "Press Enter to close this window..." _
exit "$status"
"""


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 20,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PlannerError(f"command timed out: {' '.join(command[:3])}") from exc
    except FileNotFoundError as exc:
        raise PlannerError(f"command not found: {command[0]}") from exc


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        first_newline = value.find("\n")
        if first_newline >= 0:
            value = value[first_newline + 1 :]
    if value.rstrip().endswith(fence):
        value = value.rstrip()[: -len(fence)]
    return value.strip()


def parse_model_json(text: str) -> dict[str, Any]:
    value = _strip_json_fence(text)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise PlannerError("frontier model did not return a JSON object")
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError as exc:
            raise PlannerError(f"frontier model returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise PlannerError("frontier model output must be a JSON object")
    return parsed


def validate_planner_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlannerError("planner result must be a JSON object")

    title = value.get("title")
    design = value.get("design_markdown")
    stories = value.get("stories")

    if not isinstance(title, str) or not title.strip():
        raise PlannerError("planner result is missing a title")
    if not isinstance(design, str) or not design.strip():
        raise PlannerError("planner result is missing design_markdown")
    if not isinstance(stories, list) or not stories:
        raise PlannerError("planner result must contain at least one story")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []

    for index, story in enumerate(stories, start=1):
        if not isinstance(story, dict):
            raise PlannerError(f"story {index} must be an object")

        story_id = story.get("id")
        story_title = story.get("title")
        prompt = story.get("prompt")
        allowed_paths = story.get("allowed_paths")

        if not isinstance(story_id, str) or not story_id.strip():
            raise PlannerError(f"story {index} has an invalid id")
        if story_id in seen:
            raise PlannerError(f"duplicate story id: {story_id}")
        seen.add(story_id)

        if not isinstance(story_title, str) or not story_title.strip():
            raise PlannerError(f"{story_id}: title is required")
        if not isinstance(prompt, str) or not prompt.strip():
            raise PlannerError(f"{story_id}: prompt is required")
        if not isinstance(allowed_paths, list) or not allowed_paths:
            raise PlannerError(f"{story_id}: allowed_paths must be a non-empty array")
        if not all(isinstance(path, str) and path.strip() for path in allowed_paths):
            raise PlannerError(f"{story_id}: allowed_paths contains an invalid path")

        item = {
            "id": story_id.strip(),
            "title": story_title.strip(),
            "prompt": prompt.strip(),
            "allowed_paths": [path.strip() for path in allowed_paths],
        }

        verify_commands = story.get("verify_commands")
        if verify_commands is not None:
            if not isinstance(verify_commands, list) or not all(
                isinstance(command, str) and command.strip()
                for command in verify_commands
            ):
                raise PlannerError(f"{story_id}: verify_commands must be an array of commands")
            item["verify_commands"] = [command.strip() for command in verify_commands]

        normalized.append(item)

    return {
        "title": title.strip(),
        "design_markdown": design.strip(),
        "stories": normalized,
    }


def story_budget_violations(
    stories: list[dict[str, Any]],
    repo: Path | None = None,
) -> list[str]:
    """Return local-execution budget violations for a frontier-generated story queue."""
    violations: list[str] = []

    for story in stories:
        story_id = str(story.get("id") or "?")
        paths = story.get("allowed_paths") or []
        unique_paths = list(dict.fromkeys(paths))

        if len(paths) != len(unique_paths):
            violations.append(
                f"{story_id}: allowed_paths contains duplicate paths"
            )

        if len(unique_paths) > LOCAL_STORY_HARD_MAX_PATHS:
            violations.append(
                f"{story_id}: {len(unique_paths)} editable paths exceeds hard max "
                f"{LOCAL_STORY_HARD_MAX_PATHS}"
            )

        invalid_paths = [
            path
            for path in unique_paths
            if "\\" in path or path.startswith("/") or path.startswith("./")
        ]
        if invalid_paths:
            violations.append(
                f"{story_id}: allowed_paths must be literal repo-relative paths "
                f"without regex/shell escaping: {', '.join(invalid_paths)}"
            )

        if repo is not None:
            new_paths = [
                path
                for path in unique_paths
                if "\\" not in path and not (repo / path).exists()
            ]
            if len(new_paths) > LOCAL_STORY_MAX_NEW_FILES:
                violations.append(
                    f"{story_id}: {len(new_paths)} new files exceeds hard max "
                    f"{LOCAL_STORY_MAX_NEW_FILES}: {', '.join(new_paths)}"
                )

    return violations


def format_story_budget_violations(violations: list[str]) -> str:
    return "\n".join(f"- {item}" for item in violations)


def build_story_slicer_prompt(
    requirement: str,
    project_name: str,
    title: str,
    design_markdown: str,
    stories: list[dict[str, Any]],
    violations: list[str],
) -> str:
    """Build a second-pass prompt that preserves architecture and only re-slices stories."""
    source_json = json.dumps(stories, ensure_ascii=False, indent=2)
    violation_text = format_story_budget_violations(violations)

    return f"""You are the LOCAL-EXECUTION STORY SLICER for a hybrid coding system.

PROJECT
=======
{project_name}

ORIGINAL USER REQUIREMENT
=========================
{requirement.strip()}

LOCKED ARCHITECTURE TITLE
=========================
{title}

LOCKED DESIGN
=============
{design_markdown}

CURRENT STORY QUEUE
===================
{source_json}

DETERMINISTIC BUDGET FAILURES
=============================
{violation_text}

YOUR ONLY JOB
=============
Repartition the CURRENT STORY QUEUE into smaller executable stories for a local
Qwen coding worker using Aider. Preserve the locked architecture and acceptance
criteria. Do not redesign the product.

LOCAL EXECUTION BUDGET
======================
- Target 2-4 editable files per story.
- Hard maximum: {LOCAL_STORY_HARD_MAX_PATHS} allowed_paths entries per story,
  INCLUDING test files.
- Maximum {LOCAL_STORY_MAX_NEW_FILES} new files in one story.
- allowed_paths means files expected to be EDITED, not files merely read for
  context. The coding worker has a repo map for read-only context.
- One primary subsystem or migration concern per story.
- Never introduce a new abstraction/helper and migrate every consumer in the
  same story. First establish the contract + focused tests, then migrate small
  consumer groups in later stories.
- If a new dependency changes how existing VM/test harnesses load files, make
  compatibility/harness work an explicit early story before mass migration.
- Keep storage/config, auth/session, build/deploy, worker/API, portal/navigation,
  browser regression, and documentation in separate stories unless a tiny
  atomic change genuinely requires two of them.
- Every story must leave npm test and npm run build capable of passing once the
  story is complete.
- verify_commands are ONLY extra targeted checks. Do not repeat npm test or
  npm run build there; the factory runs the base gates separately.
- Use exact repo-relative paths such as apps/dachangi/sw.js. Never write
  regex-style escapes such as sw\\.js, test\\.mjs, A\\:H, or shell globs.
- Use sequential IDs S1, S2, S3... in execution order.
- Do not create a final mega-story that is allowed to touch any file needed to
  'fix remaining issues'. Split browser regression, bug fixes, and docs/final
  verification into bounded stories.

OUTPUT
======
Return the same structured contract:
- title
- design_markdown
- stories[]

The returned title and design_markdown must be the LOCKED title/design above.
Only the story partitioning may change.
"""


def build_planner_prompt(requirement: str, project_name: str) -> str:
    request = requirement.strip()
    if not request:
        raise PlannerError("요구사항을 입력하세요.")

    return f"""You are the frontier planning model for a local autonomous coding factory.

PROJECT
=======
{project_name}

USER REQUIREMENT
================
{request}

YOUR JOB
========
Inspect the CURRENT repository in your working directory before making decisions.
Use only repository facts you can verify from source, tests, package/build scripts, and documentation.
Do not edit, create, delete, or format repository files. This is a read-only planning task.

Produce a repository-aware implementation design and an ordered queue of executable stories for a smaller local coding model.

DESIGN REQUIREMENTS
===================
The design_markdown must include:
- Goal
- Verified current state / relevant architecture
- Proposed design and important decisions
- Files/components likely involved and why
- Compatibility constraints and behavior that must not regress
- Testing/verification strategy
- Risks / edge cases
- Non-goals
- Acceptance criteria
- Story ordering/dependencies

STORY RULES
===========
These stories will be executed by a smaller local Qwen model through Aider.
Optimize for reliable autonomous execution, not for the fewest number of stories.

- Stories must be ordered so each story starts from the previous story's integrated result.
- Target 2-4 editable files per story.
- HARD MAXIMUM: {LOCAL_STORY_HARD_MAX_PATHS} allowed_paths entries per story,
  INCLUDING tests. If more files are needed, split the story.
- Maximum {LOCAL_STORY_MAX_NEW_FILES} new files in one story.
- One primary subsystem or migration concern per story.
- Do not combine a new abstraction/helper with migration of every consumer.
  First establish the helper/contract + focused tests, then migrate small groups
  of consumers in later stories.
- If a new dependency changes VM/test harness setup, create an early compatibility
  story that updates the harness before migrating more production files.
- Keep these concerns separate unless the change is genuinely tiny and atomic:
  storage/config, diary generation, photo/Drive, auth/session, build/deploy,
  worker/API, portal/navigation, service worker, browser regression, documentation.
- Each story prompt must be self-contained and implementation-oriented.
- Use real repository paths. Never invent an existing path you did not verify.
- allowed_paths is an EDIT PERMISSION LIST, not a list of every related file.
  Do not include read-only/reference files merely because the model may inspect them.
- Include a test file only when that story is expected to edit that test file.
- Use literal repo-relative paths. Never use regex/shell escaping in paths:
  write apps/dachangi/sw.js, not apps/dachangi/sw\\.js.
- Do not escape normal punctuation inside prompt text (for example write A:H,
  not A\\:H).
- Explicitly protect existing behavior and prohibit deleting, skipping, weakening,
  or bypassing tests.
- The factory already runs npm test and npm run build after every story.
  verify_commands must contain only ADDITIONAL targeted checks; do not repeat the
  base gates there.
- Every story must leave the repository in a state where the base gates can pass.
  Never create a knowingly broken intermediate migration.
- Do not ask the local model to make architectural choices already resolved in the design.
- Do not include planning-only stories. Every story must produce an implementation increment.
- Do not create a broad final "fix anything remaining" story. Browser regression,
  fixes discovered by it, docs, and final verification must stay bounded.

OUTPUT
======
Return only the requested structured result with:
- title
- design_markdown
- stories[]

Each story must contain:
- id
- title
- prompt
- allowed_paths
Optionally:
- verify_commands
"""


def build_codex_exec_command(
    executable: str,
    schema_path: Path,
    output_path: Path,
    prompt: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    """Build a read-only Codex exec command with optional model/effort overrides."""
    command = [
        executable,
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
    ]

    selected_effort = (reasoning_effort or "").strip()
    if selected_effort:
        command.extend(
            [
                "--config",
                f'model_reasoning_effort="{selected_effort}"',
            ]
        )

    command.append("exec")

    selected_model = (model or "").strip()
    if selected_model:
        command.extend(["--model", selected_model])

    command.extend(
        [
            "--ephemeral",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            prompt,
        ]
    )
    return command


class CodexAppServer:
    """Small JSONL client used only for browser-native Codex authentication."""

    def __init__(self, command: str = "codex") -> None:
        self.process = subprocess.Popen(
            [command, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._condition = threading.Condition()
        self._responses: dict[int, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._next_id = 1
        threading.Thread(target=self._read_loop, daemon=True).start()

        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "ai_hybrid_developer",
                    "title": "AI Hybrid Developer",
                    "version": "0.2.0",
                }
            },
            timeout=15,
        )
        self.notify("initialized", {})

    def _read_loop(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._condition:
                if isinstance(message.get("id"), int):
                    self._responses[message["id"]] = message
                else:
                    self._notifications.append(message)
                self._condition.notify_all()

        with self._condition:
            self._condition.notify_all()

    def _send(self, message: dict[str, Any]) -> None:
        if self.process.poll() is not None:
            raise PlannerError("Codex app-server exited unexpectedly")
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: int = 20,
    ) -> dict[str, Any]:
        with self._condition:
            request_id = self._next_id
            self._next_id += 1

        self._send({"method": method, "id": request_id, "params": params or {}})
        deadline = time.monotonic() + timeout

        with self._condition:
            while request_id not in self._responses:
                if self.process.poll() is not None:
                    raise PlannerError("Codex app-server exited before responding")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PlannerError(f"Codex app-server timed out on {method}")
                self._condition.wait(timeout=min(remaining, 0.5))
            response = self._responses.pop(request_id)

        if response.get("error"):
            error = response["error"]
            raise PlannerError(error.get("message") or f"Codex RPC error: {error}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def pop_login_result(self, login_id: str) -> dict[str, Any] | None:
        with self._condition:
            for index, message in enumerate(self._notifications):
                if message.get("method") != "account/login/completed":
                    continue
                params = message.get("params") or {}
                if params.get("loginId") != login_id:
                    continue
                self._notifications.pop(index)
                return params
        return None

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()


class PlannerService:
    def __init__(self, state_home: Path) -> None:
        self.planner_dir = state_home / "planner"
        self.planner_dir.mkdir(parents=True, exist_ok=True)
        self._codex_auth_lock = threading.Lock()
        self._codex_auth_server: CodexAppServer | None = None
        self._codex_login_id: str | None = None

    def _codex_status(self) -> dict[str, Any]:
        executable = shutil.which("codex")
        if not executable:
            return {
                "id": "codex",
                "name": "OpenAI Codex",
                "installed": False,
                "connected": False,
                "auth_mode": None,
                "plan": None,
                "models": CODEX_MODEL_OPTIONS,
                "reasoning_efforts": CODEX_REASONING_EFFORT_OPTIONS,
                "detail": "Codex CLI is not installed",
            }

        result = _run([executable, "login", "status"], timeout=12)
        text = (result.stdout + "\n" + result.stderr).strip()
        connected = result.returncode == 0
        auth_mode = None
        lowered = text.lower()
        if "chatgpt" in lowered:
            auth_mode = "chatgpt"
        elif "api key" in lowered or "apikey" in lowered:
            auth_mode = "api_key"
        elif "workload" in lowered:
            auth_mode = "workload_identity"

        plan = None
        email = None
        if connected:
            try:
                rpc = CodexAppServer(executable)
                try:
                    account = rpc.request(
                        "account/read", {"refreshToken": False}, timeout=12
                    ).get("account")
                    if isinstance(account, dict):
                        plan = account.get("planType")
                        email = account.get("email")
                        auth_mode = account.get("type") or auth_mode
                finally:
                    rpc.close()
            except PlannerError:
                pass

        return {
            "id": "codex",
            "name": "OpenAI Codex",
            "installed": True,
            "connected": connected,
            "auth_mode": auth_mode,
            "plan": plan,
            "email": email,
            "models": CODEX_MODEL_OPTIONS,
            "reasoning_efforts": CODEX_REASONING_EFFORT_OPTIONS,
            "detail": text or ("Connected" if connected else "Not logged in"),
        }

    def _gemini_status(self) -> dict[str, Any]:
        executable = find_native_executable("gemini")
        if not executable:
            return {
                "id": "gemini",
                "name": "Gemini",
                "installed": False,
                "connected": False,
                "auth_mode": None,
                "plan": None,
                "detail": (
                    "Windows Gemini CLI was detected, but this WSL dashboard requires "
                    "a native WSL Gemini CLI. Install it inside Ubuntu with "
                    "'npm install -g @google/gemini-cli'."
                    if shutil.which("gemini")
                    and _is_windows_mount_path(shutil.which("gemini") or "")
                    else "Gemini CLI is not installed"
                ),
            }

        settings_path = Path("~/.gemini/settings.json").expanduser()
        settings: dict[str, Any] = {}
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        security = settings.get("security") if isinstance(settings, dict) else {}
        security = security if isinstance(security, dict) else {}
        auth = security.get("auth") if isinstance(security, dict) else {}
        auth = auth if isinstance(auth, dict) else {}
        selected = auth.get("selectedType") or settings.get("selectedAuthType")

        oauth_cache = Path("~/.gemini/oauth_creds.json").expanduser().exists()
        api_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

        connected = (
            selected == "oauth-personal"
            and oauth_cache
            or selected == "gemini-api-key"
            and api_key
            or selected in {"USE_VERTEX_AI", "COMPUTE_ADC"}
        )
        detail = (
            "Google OAuth credentials cached"
            if selected == "oauth-personal" and oauth_cache
            else f"Configured auth: {selected}"
            if selected
            else "Gemini CLI installed; Google sign-in not detected"
        )

        return {
            "id": "gemini",
            "name": "Gemini",
            "installed": True,
            "connected": bool(connected),
            "auth_mode": selected,
            "plan": "Google AI Pro/Ultra account" if selected == "oauth-personal" else None,
            "detail": detail,
        }

    def status(self) -> dict[str, Any]:
        return {"providers": [self._codex_status(), self._gemini_status()]}

    def start_codex_login(self) -> dict[str, Any]:
        executable = shutil.which("codex")
        if not executable:
            raise PlannerError("Codex CLI가 설치되어 있지 않습니다.")

        with self._codex_auth_lock:
            if self._codex_auth_server is not None:
                self._codex_auth_server.close()

            server = CodexAppServer(executable)
            result = server.request(
                "account/login/start",
                {
                    "type": "chatgpt",
                    "useHostedLoginSuccessPage": True,
                    "appBrand": "chatgpt",
                },
                timeout=20,
            )
            auth_url = result.get("authUrl")
            login_id = result.get("loginId")
            if not auth_url or not login_id:
                server.close()
                raise PlannerError("Codex OAuth 시작 응답에 authUrl/loginId가 없습니다.")

            self._codex_auth_server = server
            self._codex_login_id = str(login_id)

        return {
            "provider": "codex",
            "pending": True,
            "auth_url": auth_url,
            "login_id": login_id,
        }

    def codex_login_status(self) -> dict[str, Any]:
        with self._codex_auth_lock:
            server = self._codex_auth_server
            login_id = self._codex_login_id

            if server is None or login_id is None:
                status = self._codex_status()
                return {
                    "pending": False,
                    "connected": status["connected"],
                    "provider": status,
                }

            result = server.pop_login_result(login_id)
            if result is None:
                return {"pending": True, "connected": False, "login_id": login_id}

            success = bool(result.get("success"))
            error = result.get("error")
            account = None
            if success:
                try:
                    account = server.request(
                        "account/read", {"refreshToken": False}, timeout=10
                    ).get("account")
                except PlannerError:
                    account = None

            server.close()
            self._codex_auth_server = None
            self._codex_login_id = None

        return {
            "pending": False,
            "connected": success,
            "error": error,
            "account": account,
        }

    def codex_logout(self) -> dict[str, Any]:
        executable = shutil.which("codex")
        if not executable:
            raise PlannerError("Codex CLI가 설치되어 있지 않습니다.")
        result = _run([executable, "logout"], timeout=20)
        if result.returncode != 0:
            raise PlannerError((result.stderr or result.stdout).strip() or "Codex logout failed")
        return {"connected": False, "detail": (result.stdout or result.stderr).strip()}

    def launch_gemini_login(self, repo: Path | None = None) -> dict[str, Any]:
        executable = find_native_executable("gemini")
        if not executable:
            raise PlannerError("Gemini CLI가 설치되어 있지 않습니다.")

        cwd = str(repo or Path.home())
        distro = os.environ.get("WSL_DISTRO_NAME")
        wt = shutil.which("wt.exe")

        script_path = self.planner_dir / "gemini-login.sh"
        script_path.write_text(
            build_gemini_login_script(executable),
            encoding="utf-8",
        )
        script_path.chmod(0o700)

        if distro and wt:
            subprocess.Popen(
                [
                    wt,
                    "wsl.exe",
                    "-d",
                    distro,
                    "--cd",
                    cwd,
                    "bash",
                    str(script_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "launched": True,
                "detail": (
                    "Gemini CLI 로그인 창을 열었습니다. "
                    "오류가 나도 창을 닫지 않고 종료 코드를 보여줍니다."
                ),
                "script": str(script_path),
            }

        terminal = shutil.which("x-terminal-emulator")
        if terminal:
            subprocess.Popen(
                [terminal, "-e", "bash", str(script_path)],
                cwd=repo,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "launched": True,
                "detail": (
                    "Gemini CLI 로그인 창을 열었습니다. "
                    "오류가 나도 창을 닫지 않고 종료 코드를 보여줍니다."
                ),
                "script": str(script_path),
            }

        raise PlannerError(
            "Gemini OAuth용 터미널을 자동으로 열 수 없습니다. 한 번만 'gemini'를 실행해 Sign in with Google을 완료하세요."
        )

    @staticmethod
    def _git_status(repo: Path) -> str:
        result = _run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo,
            timeout=20,
        )
        if result.returncode != 0:
            raise PlannerError(result.stderr.strip() or "git status failed")
        return result.stdout

    def _write_log(self, provider: str, stdout: str, stderr: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.planner_dir / f"{stamp}-{provider}.log"
        path.write_text(
            f"STDOUT\n======\n{stdout}\n\nSTDERR\n======\n{stderr}\n",
            encoding="utf-8",
        )
        return path

    def _generate_codex(
        self,
        repo: Path,
        prompt: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> tuple[dict[str, Any], Path]:
        executable = shutil.which("codex")
        if not executable:
            raise PlannerError("Codex CLI가 설치되어 있지 않습니다.")

        with tempfile.TemporaryDirectory(prefix="ai-hybrid-codex-") as tmp:
            tmp_path = Path(tmp)
            schema_path = tmp_path / "planner.schema.json"
            output_path = tmp_path / "planner-result.json"
            schema_path.write_text(json.dumps(PLANNER_SCHEMA, indent=2), encoding="utf-8")

            command = build_codex_exec_command(
                executable,
                schema_path,
                output_path,
                prompt,
                model,
                reasoning_effort,
            )
            result = _run(command, cwd=repo, timeout=1200)
            log = self._write_log("codex", result.stdout, result.stderr)

            if result.returncode != 0:
                raise PlannerError(
                    (result.stderr or result.stdout).strip()
                    or f"Codex planner failed with exit {result.returncode}"
                )
            if not output_path.exists():
                raise PlannerError("Codex planner did not create its structured output file")

            payload = parse_model_json(output_path.read_text(encoding="utf-8"))
            return validate_planner_result(payload), log

    def _generate_gemini(self, repo: Path, prompt: str) -> tuple[dict[str, Any], Path]:
        executable = find_native_executable("gemini")
        if not executable:
            raise PlannerError("Gemini CLI가 설치되어 있지 않습니다.")

        gemini_prompt = (
            prompt
            + "\n\nGemini CLI note: Return the final result as one raw JSON object only. "
            "Do not wrap it in Markdown fences. The object must contain exactly "
            "title, design_markdown, and stories according to the requested format."
        )
        command = [
            executable,
            "--approval-mode",
            "plan",
            "--output-format",
            "json",
            "--prompt",
            gemini_prompt,
        ]
        result = _run(command, cwd=repo, timeout=1200)
        log = self._write_log("gemini", result.stdout, result.stderr)

        if result.returncode != 0:
            try:
                envelope = json.loads(result.stdout)
                error = envelope.get("error")
                if isinstance(error, dict) and error.get("message"):
                    raise PlannerError(str(error["message"]))
            except json.JSONDecodeError:
                pass
            raise PlannerError(
                (result.stderr or result.stdout).strip()
                or f"Gemini planner failed with exit {result.returncode}"
            )

        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PlannerError(f"Gemini CLI returned invalid JSON envelope: {exc}") from exc

        error = envelope.get("error")
        if isinstance(error, dict) and error:
            raise PlannerError(str(error.get("message") or error))

        response = envelope.get("response")
        if not isinstance(response, str) or not response.strip():
            raise PlannerError("Gemini CLI returned an empty response")

        payload = parse_model_json(response)
        return validate_planner_result(payload), log

    def _generate_for_provider(
        self,
        *,
        provider: str,
        repo: Path,
        prompt: str,
        model: str | None,
        reasoning_effort: str | None,
    ) -> tuple[dict[str, Any], Path]:
        if provider == "codex":
            return self._generate_codex(
                repo,
                prompt,
                model,
                reasoning_effort,
            )
        if provider == "gemini":
            return self._generate_gemini(repo, prompt)
        raise PlannerError(f"unsupported planner provider: {provider}")

    def generate(
        self,
        *,
        provider: str,
        project_name: str,
        source_repo: str,
        requirement: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        repo = Path(source_repo).expanduser().resolve()
        if not repo.exists():
            raise PlannerError(f"project repository not found: {repo}")

        prompt = build_planner_prompt(requirement, project_name)
        before = self._git_status(repo)
        started = time.monotonic()

        result, log = self._generate_for_provider(
            provider=provider,
            repo=repo,
            prompt=prompt,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        logs = [str(log)]

        locked_title = result["title"]
        locked_design = result["design_markdown"]
        violations = story_budget_violations(result["stories"], repo)
        reslice_passes = 0

        while violations and reslice_passes < LOCAL_STORY_MAX_RESLICE_PASSES:
            reslice_passes += 1
            slicer_prompt = build_story_slicer_prompt(
                requirement,
                project_name,
                locked_title,
                locked_design,
                result["stories"],
                violations,
            )
            sliced, slice_log = self._generate_for_provider(
                provider=provider,
                repo=repo,
                prompt=slicer_prompt,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            logs.append(str(slice_log))

            # Architecture is owned by the first planning pass. The slicer may only
            # repartition implementation work.
            sliced["title"] = locked_title
            sliced["design_markdown"] = locked_design
            result = sliced
            violations = story_budget_violations(result["stories"], repo)

        if violations:
            raise PlannerError(
                "Frontier planner가 local story budget을 "
                f"{LOCAL_STORY_MAX_RESLICE_PASSES}회 재분해 후에도 만족하지 못했습니다.\n"
                + format_story_budget_violations(violations)
            )

        after = self._git_status(repo)
        if after != before:
            raise PlannerError(
                "Frontier planner가 read-only 규칙을 어기고 repository 상태를 변경했습니다. "
                f"변경사항은 자동 적용하지 않았습니다. 로그: {logs[-1]}"
            )

        return {
            **result,
            "provider": provider,
            "model": (model or "").strip() or None,
            "reasoning_effort": (reasoning_effort or "").strip() or None,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "log": logs[-1],
            "logs": logs,
            "story_budget": {
                "status": "PASS",
                "target_max_paths": LOCAL_STORY_TARGET_MAX_PATHS,
                "hard_max_paths": LOCAL_STORY_HARD_MAX_PATHS,
                "max_new_files": LOCAL_STORY_MAX_NEW_FILES,
                "reslice_passes": reslice_passes,
                "story_count": len(result["stories"]),
            },
        }
