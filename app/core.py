from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_FACTORY_HOME = Path("~/local-coding-factory").expanduser()
DEFAULT_STATE_HOME = Path("~/.local/state/ai-hybrid-developer").expanduser()


class ControlPlaneError(RuntimeError):
    """Expected, user-facing control plane error."""


class ConflictError(ControlPlaneError):
    """Raised when an operation conflicts with an active job."""


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def validate_stories(stories: Any) -> list[dict[str, Any]]:
    if not isinstance(stories, list) or not stories:
        raise ControlPlaneError("stories must be a non-empty JSON array")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []

    for index, story in enumerate(stories):
        if not isinstance(story, dict):
            raise ControlPlaneError(f"story {index + 1} must be an object")

        for key in ("id", "prompt", "allowed_paths"):
            if key not in story:
                raise ControlPlaneError(f"story {index + 1} is missing '{key}'")

        story_id = story["id"]
        if not isinstance(story_id, str) or not story_id.strip():
            raise ControlPlaneError(f"story {index + 1} has an invalid id")
        if story_id in seen:
            raise ControlPlaneError(f"duplicate story id: {story_id}")
        seen.add(story_id)

        prompt = story["prompt"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise ControlPlaneError(f"{story_id}: prompt must be non-empty text")

        allowed_paths = story["allowed_paths"]
        if not isinstance(allowed_paths, list) or not all(
            isinstance(path, str) and path.strip() for path in allowed_paths
        ):
            raise ControlPlaneError(f"{story_id}: allowed_paths must be a list of paths")

        normalized.append(dict(story))

    return normalized


def compile_runtime_stories(
    stories: list[dict[str, Any]], design_md: str
) -> list[dict[str, Any]]:
    design = design_md.strip()
    if not design:
        return [dict(story) for story in stories]

    compiled: list[dict[str, Any]] = []
    for story in stories:
        item = dict(story)
        item["prompt"] = (
            "SHARED DESIGN SPECIFICATION\n"
            "===========================\n"
            f"{design}\n\n"
            "STORY-SPECIFIC REQUIREMENTS\n"
            "===========================\n"
            f"{story['prompt']}"
        )
        compiled.append(item)
    return compiled


def tail_file(path: Path, max_bytes: int = 120_000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(-max_bytes, os.SEEK_END)
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class Settings:
    factory_home: Path
    state_home: Path
    factory_command: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            factory_home=Path(
                os.environ.get("FACTORY_HOME", str(DEFAULT_FACTORY_HOME))
            ).expanduser(),
            state_home=Path(
                os.environ.get("AI_HYBRID_STATE_HOME", str(DEFAULT_STATE_HOME))
            ).expanduser(),
            factory_command=os.environ.get("FACTORY_COMMAND", "factory"),
        )


class FactoryControl:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.state_home.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.settings.state_home / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.settings.state_home / "processes.json"

    @property
    def projects_dir(self) -> Path:
        return self.settings.factory_home / "projects"

    @property
    def runs_dir(self) -> Path:
        return self.settings.factory_home / "runs"

    @property
    def package_dir(self) -> Path:
        return self.settings.factory_home / "inbox" / "packages"

    def _load_registry(self) -> dict[str, Any]:
        return read_json(self.registry_path, {}) or {}

    def _save_registry(self, registry: dict[str, Any]) -> None:
        atomic_write_json(self.registry_path, registry)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists():
                fields = stat.read_text(encoding="utf-8", errors="replace").split()
                if len(fields) >= 3 and fields[2] == "Z":
                    return False
            os.kill(pid, 0)
            return True
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return False

    def _job_info(self, project: str) -> dict[str, Any] | None:
        registry = self._load_registry()
        job = registry.get(project)
        if not job:
            return None
        pid = int(job.get("pid", 0))
        alive = pid > 0 and self._pid_alive(pid)
        if job.get("alive") != alive:
            job["alive"] = alive
            registry[project] = job
            self._save_registry(registry)
        return job

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        if not self.projects_dir.exists():
            return projects

        for path in sorted(self.projects_dir.glob("*.toml")):
            try:
                with path.open("rb") as handle:
                    cfg = tomllib.load(handle)
            except Exception as exc:
                projects.append(
                    {"name": path.stem, "config_error": str(exc), "path": str(path)}
                )
                continue

            project = cfg.get("project", {})
            models = cfg.get("models", {})
            projects.append(
                {
                    "name": path.stem,
                    "path": str(path),
                    "source_repo": project.get("source_repo"),
                    "base_ref": project.get("base_ref", "main"),
                    "developer_model": models.get("developer"),
                    "reviewer_model": models.get("reviewer"),
                }
            )
        return projects

    def _current_meta(self, project: str) -> dict[str, Any] | None:
        return read_json(self.runs_dir / project / "current.json")

    def status(self, project: str) -> dict[str, Any]:
        meta = self._current_meta(project)
        job = self._job_info(project)
        payload: dict[str, Any] = {
            "project": project,
            "process": job,
            "run": None,
            "status": "not_started",
            "next_index": 0,
            "completed": [],
            "inflight": None,
            "stories": [],
        }

        if not meta:
            return payload

        runtime = Path(meta.get("runtime", ""))
        state = read_json(runtime / "state.json", {}) or {}
        stories_path = Path(meta.get("stories", ""))
        stories = read_json(stories_path, []) or []
        completed_ids = {item.get("id") for item in state.get("completed", [])}
        inflight = state.get("inflight")
        inflight_id = inflight.get("id") if isinstance(inflight, dict) else None

        story_rows = []
        for story in stories:
            story_id = story.get("id")
            if story_id in completed_ids:
                story_status = "complete"
            elif story_id == inflight_id:
                story_status = inflight.get("phase", "running")
            else:
                story_status = "pending"
            story_rows.append(
                {
                    "id": story_id,
                    "title": story.get("title", story_id),
                    "status": story_status,
                }
            )

        payload.update(
            {
                "run": meta,
                "status": state.get("status", "created"),
                "next_index": state.get("next_index", 0),
                "completed": state.get("completed", []),
                "inflight": inflight,
                "stories": story_rows,
            }
        )
        return payload

    def create_package(
        self,
        project: str,
        *,
        title: str,
        design_md: str,
        stories: Any,
    ) -> dict[str, Any]:
        source_stories = validate_stories(stories)
        runtime_stories = compile_runtime_stories(source_stories, design_md)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_project = "".join(
            char if char.isalnum() or char in "._-" else "-" for char in project
        )
        package = self.package_dir / safe_project / timestamp
        package.mkdir(parents=True, exist_ok=False)

        (package / "design.md").write_text(
            design_md.strip() + ("\n" if design_md.strip() else ""), encoding="utf-8"
        )
        atomic_write_json(package / "stories.source.json", source_stories)
        atomic_write_json(package / "stories.json", runtime_stories)

        manifest = (
            "[package]\n"
            f"project = {toml_string(project)}\n"
            f"title = {toml_string(title.strip() or 'Untitled work package')}\n"
            f"created_at = {toml_string(timestamp)}\n"
            'design_file = "design.md"\n'
            'source_stories_file = "stories.source.json"\n'
            'runtime_stories_file = "stories.json"\n'
        )
        (package / "package.toml").write_text(manifest, encoding="utf-8")

        return {
            "path": str(package),
            "design": str(package / "design.md"),
            "stories": str(package / "stories.json"),
            "story_count": len(runtime_stories),
        }

    def _spawn(
        self, project: str, command: list[str], *, package: str | None = None
    ) -> dict[str, Any]:
        current = self._job_info(project)
        if current and current.get("alive"):
            raise ConflictError(f"{project} already has an active factory process")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.log_dir / f"{project}-{stamp}.log"
        log_handle = log_path.open("ab", buffering=0)
        try:
            proc = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(self.settings.factory_home),
            )
        finally:
            log_handle.close()

        job = {
            "pid": proc.pid,
            "alive": True,
            "started_at": stamp,
            "command": command,
            "log": str(log_path),
            "package": package,
        }
        registry = self._load_registry()
        registry[project] = job
        self._save_registry(registry)
        return job

    def start_run(
        self, project: str, *, title: str, design_md: str, stories: Any
    ) -> dict[str, Any]:
        if project not in {item["name"] for item in self.list_projects()}:
            raise ControlPlaneError(f"unknown factory project: {project}")
        package = self.create_package(
            project, title=title, design_md=design_md, stories=stories
        )
        command = [self.settings.factory_command, "run", project, package["stories"]]
        job = self._spawn(project, command, package=package["path"])
        return {"package": package, "process": job}

    def resume(self, project: str) -> dict[str, Any]:
        command = [self.settings.factory_command, "resume", project]
        return self._spawn(project, command)

    def stop(self, project: str) -> dict[str, Any]:
        job = self._job_info(project)
        if not job or not job.get("alive"):
            return {"stopped": False, "reason": "no active process"}
        pid = int(job["pid"])
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        deadline = time.time() + 5
        while time.time() < deadline and self._pid_alive(pid):
            time.sleep(0.1)
        if self._pid_alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        registry = self._load_registry()
        if project in registry:
            registry[project]["alive"] = False
            registry[project]["stopped_at"] = datetime.now().isoformat(
                timespec="seconds"
            )
            self._save_registry(registry)
        return {"stopped": True, "pid": pid}

    def logs(self, project: str) -> dict[str, Any]:
        job = self._job_info(project)
        if not job:
            return {"log": "", "path": None}
        path = Path(job["log"])
        return {"log": tail_file(path), "path": str(path)}

    def diff(self, project: str) -> dict[str, Any]:
        meta = self._current_meta(project)
        if not meta:
            return {"diff": "", "stat": "", "integration": None}

        integration = Path(meta.get("integration", ""))
        runtime = Path(meta.get("runtime", ""))
        state = read_json(runtime / "state.json", {}) or {}
        base = state.get("base_commit")
        if not base or not integration.exists():
            return {"diff": "", "stat": "", "integration": str(integration)}

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=integration,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if head.returncode != 0:
            raise ControlPlaneError(head.stderr.strip() or "git rev-parse failed")

        diff = subprocess.run(
            ["git", "diff", f"{base}..{head.stdout.strip()}"],
            cwd=integration,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stat = subprocess.run(
            ["git", "diff", "--stat", f"{base}..{head.stdout.strip()}"],
            cwd=integration,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return {
            "diff": diff.stdout if diff.returncode == 0 else diff.stderr,
            "stat": stat.stdout if stat.returncode == 0 else stat.stderr,
            "integration": str(integration),
            "branch": meta.get("branch"),
        }

    def open_vscode(self, project: str) -> dict[str, Any]:
        meta = self._current_meta(project)
        if not meta:
            raise ControlPlaneError("no current run")
        integration = Path(meta.get("integration", ""))
        if not integration.exists():
            raise ControlPlaneError("integration worktree no longer exists")
        try:
            subprocess.Popen(
                ["code", "-r", str(integration)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise ControlPlaneError("VS Code command 'code' is not installed in WSL") from exc
        return {"opened": True, "integration": str(integration)}
