from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .core import ConflictError, ControlPlaneError, FactoryControl, Settings
from .planner import PlannerError, PlannerService
from .planner_api import PlannerApi


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"
MAX_BODY = 10 * 1024 * 1024


class DashboardHandler(SimpleHTTPRequestHandler):
    control: FactoryControl
    planner_api: PlannerApi

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ControlPlaneError("request body is too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ControlPlaneError(f"invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ControlPlaneError("request body must be a JSON object")
        return data

    @staticmethod
    def _segments(path: str) -> list[str]:
        return [urllib.parse.unquote(part) for part in path.split("/") if part]

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            if parsed.path == "/":
                self.path = "/index.html"
            return super().do_GET()

        try:
            parts = self._segments(parsed.path)

            planner_response = self.planner_api.get(parts)
            if planner_response is not None:
                status, payload = planner_response
                return self._json(status, payload)

            if parts == ["api", "health"]:
                return self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "factory_home": str(self.control.settings.factory_home),
                        "factory_command": self.control.settings.factory_command,
                    },
                )
            if parts == ["api", "projects"]:
                return self._json(
                    HTTPStatus.OK,
                    {"projects": self.control.list_projects()},
                )
            if len(parts) == 4 and parts[:2] == ["api", "projects"]:
                project, action = parts[2], parts[3]
                if action == "status":
                    return self._json(
                        HTTPStatus.OK,
                        self.control.status(project),
                    )
                if action == "logs":
                    return self._json(
                        HTTPStatus.OK,
                        self.control.logs(project),
                    )
                if action == "diff":
                    return self._json(
                        HTTPStatus.OK,
                        self.control.diff(project),
                    )
            return self._json(
                HTTPStatus.NOT_FOUND,
                {"error": "unknown API route"},
            )
        except (ControlPlaneError, PlannerError) as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            print(f"API error: {exc!r}", file=sys.stderr)
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)},
            )

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        try:
            parts = self._segments(parsed.path)
            body = self._read_json()

            planner_response = self.planner_api.post(parts, body)
            if planner_response is not None:
                status, payload = planner_response
                return self._json(status, payload)

            if len(parts) != 4 or parts[:2] != ["api", "projects"]:
                return self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "unknown API route"},
                )

            project, action = parts[2], parts[3]

            if action == "runs":
                result = self.control.start_run(
                    project,
                    title=str(body.get("title", "")),
                    design_md=str(body.get("design_md", "")),
                    stories=body.get("stories"),
                )
                return self._json(HTTPStatus.ACCEPTED, result)
            if action == "resume":
                return self._json(
                    HTTPStatus.ACCEPTED,
                    self.control.resume(project),
                )
            if action == "stop":
                return self._json(
                    HTTPStatus.OK,
                    self.control.stop(project),
                )
            if action == "open-vscode":
                return self._json(
                    HTTPStatus.OK,
                    self.control.open_vscode(project),
                )

            return self._json(
                HTTPStatus.NOT_FOUND,
                {"error": "unknown API route"},
            )
        except ConflictError as exc:
            return self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except (ControlPlaneError, PlannerError) as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            print(f"API error: {exc!r}", file=sys.stderr)
            return self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)},
            )


def open_browser(url: str) -> None:
    try:
        if os.environ.get("WSL_DISTRO_NAME") and shutil_which("cmd.exe"):
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            webbrowser.open(url)
    except Exception as exc:
        print(f"Could not open browser automatically: {exc}")


def shutil_which(command: str) -> str | None:
    from shutil import which

    return which(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-hybrid-developer")
    parser.add_argument(
        "--host",
        default=os.environ.get("AI_HYBRID_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AI_HYBRID_PORT", "8787")),
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the dashboard in the default browser",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    control = FactoryControl(settings)
    planner = PlannerService(settings.state_home)

    DashboardHandler.control = control
    DashboardHandler.planner_api = PlannerApi(control, planner)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print("AI Hybrid Developer")
    print(f"Dashboard: {url}")
    print(f"Factory home: {settings.factory_home}")
    print("Frontier planners: OpenAI Codex + Gemini CLI")
    print(
        "Press Ctrl+C to stop the web control plane. "
        "Factory jobs run in their own process group."
    )

    if args.open:
        open_browser(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
