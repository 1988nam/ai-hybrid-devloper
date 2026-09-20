from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

from .core import ControlPlaneError, FactoryControl
from .planner import PlannerError, PlannerService


class PlannerApi:
    def __init__(self, control: FactoryControl, planner: PlannerService) -> None:
        self.control = control
        self.planner = planner

    def _project_info(self, name: str) -> dict[str, Any]:
        for project in self.control.list_projects():
            if project.get("name") == name:
                if project.get("config_error"):
                    raise ControlPlaneError(str(project["config_error"]))
                return project
        raise ControlPlaneError(f"unknown factory project: {name}")

    def get(
        self,
        parts: list[str],
    ) -> tuple[int, dict[str, Any]] | None:
        if parts == ["api", "planners"]:
            return HTTPStatus.OK, self.planner.status()

        if parts == ["api", "planners", "codex", "login-status"]:
            return HTTPStatus.OK, self.planner.codex_login_status()

        return None

    def post(
        self,
        parts: list[str],
        body: dict[str, Any],
    ) -> tuple[int, dict[str, Any]] | None:
        if parts == ["api", "planners", "codex", "connect"]:
            return HTTPStatus.ACCEPTED, self.planner.start_codex_login()

        if parts == ["api", "planners", "codex", "logout"]:
            return HTTPStatus.OK, self.planner.codex_logout()

        if parts == ["api", "planners", "gemini", "connect"]:
            project_name = str(body.get("project", ""))
            repo = None
            if project_name:
                source_repo = self._project_info(project_name).get("source_repo")
                if source_repo:
                    repo = Path(str(source_repo)).expanduser()
            return HTTPStatus.ACCEPTED, self.planner.launch_gemini_login(repo)

        if (
            len(parts) == 4
            and parts[:2] == ["api", "projects"]
            and parts[3] == "plan"
        ):
            project = parts[2]
            info = self._project_info(project)
            source_repo = info.get("source_repo")
            if not source_repo:
                raise PlannerError(f"{project}: source_repo is not configured")

            result = self.planner.generate(
                provider=str(body.get("provider", "")),
                project_name=project,
                source_repo=str(source_repo),
                requirement=str(body.get("requirement", "")),
                model=str(body.get("model", "")).strip() or None,
            )
            return HTTPStatus.OK, result

        return None
