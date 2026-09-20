#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request

from datetime import datetime
from pathlib import Path


HOME = Path.home()
FACTORY_HOME = Path(
    os.environ.get(
        "FACTORY_HOME",
        str(HOME / "local-coding-factory"),
    )
).expanduser().resolve()
PROJECTS = FACTORY_HOME / "projects"
RUNS = FACTORY_HOME / "runs"
RUNNER = FACTORY_HOME / "runner.py"


def atomic_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    os.replace(tmp, path)


def expand_path(value):
    return Path(
        os.path.expandvars(
            os.path.expanduser(value)
        )
    ).resolve()


def run(
    cmd,
    cwd=None,
    capture=False,
    check=False,
    env=None,
):
    print(
        "+",
        " ".join(map(str, cmd)),
        flush=True,
    )

    p = subprocess.run(
        list(map(str, cmd)),
        cwd=cwd,
        env=env,
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
    )

    if check and p.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(map(str, cmd))}\n"
            f"{p.stdout or ''}\n"
            f"{p.stderr or ''}"
        )

    return p


def load_config(name):
    path = PROJECTS / f"{name}.toml"

    if not path.exists():
        raise RuntimeError(
            f"unknown project: {name}\n"
            f"missing: {path}"
        )

    with path.open("rb") as f:
        cfg = tomllib.load(f)

    return cfg


def stories_hash(path):
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def validate_stories(path):
    try:
        stories = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as error:
        raise RuntimeError(
            f"invalid stories JSON: {error}"
        )

    if not isinstance(stories, list):
        raise RuntimeError(
            "stories JSON must be an array"
        )

    seen = set()

    for index, story in enumerate(stories):
        if not isinstance(story, dict):
            raise RuntimeError(
                f"story {index} is not an object"
            )

        for key in (
            "id",
            "prompt",
            "allowed_paths",
        ):
            if key not in story:
                raise RuntimeError(
                    f"story {index} missing {key}"
                )

        if story["id"] in seen:
            raise RuntimeError(
                f"duplicate story id: "
                f"{story['id']}"
            )

        seen.add(story["id"])

        if not isinstance(
            story["allowed_paths"],
            list,
        ):
            raise RuntimeError(
                f"{story['id']}: "
                "allowed_paths must be an array"
            )

    return stories


def endpoint_ok(base):
    try:
        with urllib.request.urlopen(
            base.rstrip("/") + "/api/tags",
            timeout=3,
        ) as response:
            return response.status == 200

    except Exception:
        return False


def ensure_ollama(cfg):
    base = cfg["models"]["ollama"]

    if endpoint_ok(base):
        print(
            "OLLAMA_OK:",
            base,
        )
        return

    tunnel = cfg.get(
        "tunnel",
        {},
    )

    if not tunnel.get(
        "enabled",
        False,
    ):
        raise RuntimeError(
            f"Ollama is unreachable: {base}"
        )

    host = tunnel["host"]
    user = tunnel["user"]
    local_port = int(
        tunnel.get(
            "local_port",
            11436,
        )
    )

    remote_port = int(
        tunnel.get(
            "remote_port",
            11434,
        )
    )

    print(
        "Ollama tunnel is not active."
    )
    print(
        "Starting SSH tunnel "
        f"{user}@{host} ..."
    )
    print(
        "SSH password may be requested "
        "if key authentication is not configured."
    )

    p = subprocess.run(
        [
            "ssh",
            "-fNT",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-L",
            (
                f"127.0.0.1:{local_port}:"
                f"127.0.0.1:{remote_port}"
            ),
            f"{user}@{host}",
        ]
    )

    if p.returncode != 0:
        raise RuntimeError(
            "could not start SSH tunnel"
        )

    for _ in range(20):
        if endpoint_ok(base):
            print(
                "OLLAMA_TUNNEL_OK:",
                base,
            )
            return

        time.sleep(0.5)

    raise RuntimeError(
        "SSH tunnel started but "
        "Ollama is still unreachable"
    )


def current_meta_path(name):
    return (
        RUNS
        / name
        / "current.json"
    )


def load_current(name):
    path = current_meta_path(name)

    if not path.exists():
        return None

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def load_state(meta):
    path = (
        Path(meta["runtime"])
        / "state.json"
    )

    if not path.exists():
        return None

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def source_clean(repo):
    p = run(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        cwd=repo,
        capture=True,
        check=True,
    )

    return not p.stdout.strip()


def ensure_node_modules_link(
    integration,
    node_modules,
):
    if not node_modules:
        return

    source = expand_path(
        node_modules
    )

    if not source.exists():
        print(
            "WARNING: configured "
            f"node_modules missing: {source}"
        )
        return

    target = integration / "node_modules"

    if (
        target.exists()
        or target.is_symlink()
    ):
        if (
            target.is_dir()
            and not target.is_symlink()
        ):
            shutil.rmtree(target)
        else:
            target.unlink()

    target.symlink_to(
        source,
        target_is_directory=True,
    )

    p = run(
        [
            "git",
            "rev-parse",
            "--git-path",
            "info/exclude",
        ],
        cwd=integration,
        capture=True,
        check=True,
    )

    exclude = Path(
        p.stdout.strip()
    )

    existing = (
        exclude.read_text(
            encoding="utf-8"
        )
        if exclude.exists()
        else ""
    )

    if "/node_modules" not in existing.splitlines():
        with exclude.open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                "\n/node_modules\n"
            )


def start_run(name, cfg, stories_path):
    project = cfg["project"]

    source_repo = expand_path(
        project["source_repo"]
    )

    if not source_repo.exists():
        raise RuntimeError(
            f"source repo missing: "
            f"{source_repo}"
        )

    if not source_clean(source_repo):
        raise RuntimeError(
            "source repo has tracked "
            "uncommitted changes.\n"
            "Commit/stash them before "
            "starting a new factory run."
        )

    base_ref = project.get(
        "base_ref",
        "main",
    )

    validate_stories(
        stories_path
    )

    run_id = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    run_dir = (
        RUNS
        / name
        / run_id
    )

    integration = (
        run_dir
        / "integration"
    )

    runtime = (
        run_dir
        / "runtime"
    )

    worktrees = (
        run_dir
        / "worktrees"
    )

    run_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    frozen_stories = (
        run_dir
        / "stories.json"
    )

    shutil.copy2(
        stories_path,
        frozen_stories,
    )

    branch = (
        f"factory/{name}/{run_id}"
    )

    run(
        [
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(integration),
            base_ref,
        ],
        cwd=source_repo,
        check=True,
    )

    ensure_node_modules_link(
        integration,
        project.get(
            "node_modules",
        ),
    )

    meta = {
        "project": name,
        "run_id": run_id,
        "branch": branch,
        "source_repo": str(
            source_repo
        ),
        "integration": str(
            integration
        ),
        "runtime": str(
            runtime
        ),
        "worktrees": str(
            worktrees
        ),
        "stories": str(
            frozen_stories
        ),
        "stories_sha256":
            stories_hash(
                stories_path
            ),
        "created_at": run_id,
    }

    atomic_json(
        current_meta_path(name),
        meta,
    )

    print()
    print(
        "NEW_FACTORY_RUN:",
        run_id,
    )
    print(
        "branch:",
        branch,
    )
    print(
        "integration:",
        integration,
    )

    return meta


def runner_env(cfg):
    verify = cfg.get(
        "verify",
        {},
    )

    project = cfg[
        "project"
    ]

    models = cfg[
        "models"
    ]

    env = os.environ.copy()

    env[
        "FACTORY_DEV_MODEL"
    ] = models[
        "developer"
    ]

    env[
        "FACTORY_REVIEW_MODEL"
    ] = models[
        "reviewer"
    ]

    env[
        "FACTORY_VERIFY_COMMANDS_JSON"
    ] = json.dumps(
        verify.get(
            "commands",
            [
                "npm test",
                "npm run build",
            ],
        )
    )

    env[
        "FACTORY_MAX_DEV_ATTEMPTS"
    ] = str(
        verify.get(
            "max_dev_attempts",
            3,
        )
    )

    env[
        "FACTORY_MAX_REVIEW_PROTOCOL_ATTEMPTS"
    ] = str(
        verify.get(
            "max_review_protocol_attempts",
            2,
        )
    )

    env[
        "FACTORY_DEV_TIMEOUT"
    ] = str(
        verify.get(
            "dev_timeout",
            1800,
        )
    )

    env[
        "FACTORY_VERIFY_TIMEOUT"
    ] = str(
        verify.get(
            "verify_timeout",
            900,
        )
    )

    env[
        "FACTORY_REVIEW_TIMEOUT"
    ] = str(
        verify.get(
            "review_timeout",
            600,
        )
    )

    env[
        "FACTORY_TEST_PATHS"
    ] = ",".join(
        project.get(
            "test_paths",
            ["tests"],
        )
    )

    env[
        "FACTORY_TEST_GUARD"
    ] = "1"

    return env


def run_meta(meta, cfg):
    integration = Path(
        meta["integration"]
    )

    if not integration.exists():
        raise RuntimeError(
            "integration worktree "
            "does not exist:\n"
            f"{integration}"
        )

    cmd = [
        sys.executable,
        str(RUNNER),
        "--repo",
        str(integration),
        "--stories",
        meta["stories"],
        "--integration-branch",
        meta["branch"],
        "--runtime",
        meta["runtime"],
        "--worktree-root",
        meta["worktrees"],
        "--ollama",
        cfg["models"]["ollama"],
    ]

    node_modules = cfg[
        "project"
    ].get(
        "node_modules"
    )

    if node_modules:
        cmd += [
            "--node-modules",
            str(
                expand_path(
                    node_modules
                )
            ),
        ]

    print()
    print(
        "FACTORY_RUN:",
        meta["run_id"],
    )
    print()

    p = subprocess.run(
        cmd,
        env=runner_env(cfg),
    )

    print()
    print(
        "FACTORY_EXIT=",
        p.returncode,
        sep="",
    )

    return p.returncode


def command_run(args):
    cfg = load_config(
        args.project
    )

    ensure_ollama(cfg)

    current = load_current(
        args.project
    )

    if current:
        state = load_state(
            current
        )

        if (
            state
            and state.get("status")
            != "complete"
        ):
            print(
                "Incomplete run found; "
                "resuming it."
            )

            return run_meta(
                current,
                cfg,
            )

    stories_path = (
        expand_path(
            args.stories
        )
        if args.stories
        else expand_path(
            cfg["project"][
                "default_stories"
            ]
        )
    )

    if not stories_path.exists():
        raise RuntimeError(
            f"stories file missing: "
            f"{stories_path}"
        )

    new_hash = stories_hash(
        stories_path
    )

    if current:
        state = load_state(
            current
        )

        if (
            state
            and state.get("status")
            == "complete"
            and current.get(
                "stories_sha256"
            )
            == new_hash
            and not args.force_new
        ):
            print(
                "The latest run already "
                "completed this exact stories file."
            )
            print(
                "Update the stories file, or use "
                "--force-new to intentionally rerun it."
            )
            return 3

    meta = start_run(
        args.project,
        cfg,
        stories_path,
    )

    return run_meta(
        meta,
        cfg,
    )


def command_resume(args):
    cfg = load_config(
        args.project
    )

    ensure_ollama(cfg)

    current = load_current(
        args.project
    )

    if not current:
        raise RuntimeError(
            "no previous run to resume"
        )

    return run_meta(
        current,
        cfg,
    )


def command_status(args):
    current = load_current(
        args.project
    )

    if not current:
        print(
            f"{args.project}: no factory runs"
        )
        return 0

    state = load_state(
        current
    )

    print(
        "project       :",
        args.project,
    )
    print(
        "run           :",
        current["run_id"],
    )
    print(
        "branch        :",
        current["branch"],
    )
    print(
        "integration   :",
        current["integration"],
    )

    if not state:
        print(
            "status        : not started"
        )

        return 0

    print(
        "status        :",
        state.get("status"),
    )
    print(
        "next_index    :",
        state.get("next_index"),
    )
    print(
        "completed     :",
        ", ".join(
            item["id"]
            for item
            in state.get(
                "completed",
                [],
            )
        ) or "-",
    )

    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        prog="factory"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    run_p = sub.add_parser(
        "run",
        help=(
            "start a new run or "
            "resume an incomplete one"
        ),
    )

    run_p.add_argument(
        "project",
    )

    run_p.add_argument(
        "stories",
        nargs="?",
    )

    run_p.add_argument(
        "--force-new",
        action="store_true",
    )

    resume_p = sub.add_parser(
        "resume",
    )

    resume_p.add_argument(
        "project",
    )

    status_p = sub.add_parser(
        "status",
    )

    status_p.add_argument(
        "project",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    try:
        if args.command == "run":
            return command_run(
                args
            )

        if args.command == "resume":
            return command_resume(
                args
            )

        if args.command == "status":
            return command_status(
                args
            )

        return 2

    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130

    except Exception as error:
        print()
        print(
            "FACTORY_ERROR:",
            error,
        )
        return 40


if __name__ == "__main__":
    sys.exit(main())
