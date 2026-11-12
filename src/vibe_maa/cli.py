"""JSON command-line client for the persistent project-local development service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .daemon import daemon_status, envelope, project_lock, request, stop_daemon
from .errors import DevError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise DevError("INVALID_ARGUMENT", message)


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a JSON object.") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("Expected a JSON object.")
    return parsed


def _box(value: str) -> list[int]:
    try:
        parts = [int(part) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected x,y,width,height.") from exc
    if len(parts) != 4 or min(parts[:2]) < 0 or min(parts[2:]) <= 0:
        raise argparse.ArgumentTypeError("Expected nonnegative x,y and positive width,height.")
    return parts


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="vibe-maa", description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize a standard MaaFW project without overwrites.")
    capabilities = commands.add_parser(
        "capabilities",
        help="Discover operations and exact parameter schemas without connecting a device.",
    )
    capabilities.add_argument("--method")
    context = commands.add_parser(
        "context",
        help="Read a bounded project/task snapshot for an AI without starting a device session.",
    )
    context.add_argument("--task")
    context.add_argument("--max-bytes", type=int, default=12000)
    reference = commands.add_parser(
        "reference", help="Read concise offline MaaFW protocol guidance."
    )
    reference.add_argument(
        "--topic", choices=["pipeline", "recognition", "actions", "errors"], default="pipeline"
    )

    daemon = commands.add_parser("daemon", help="Inspect or stop the project-local daemon.")
    daemon.add_argument("operation", choices=["status", "stop"])

    sessions = commands.add_parser("session")
    session_commands = sessions.add_subparsers(dest="operation", required=True)
    opening = session_commands.add_parser("open")
    opening.add_argument("--backend", choices=["demo", "adb", "browser"], required=True)
    opening.add_argument("--device", default="")
    opening.add_argument("--adb-path", default="adb")
    opening.add_argument("--resource-path", default="resource")
    opening.add_argument("--initial-state", choices=["home", "mailbox", "popup"], default="home")
    opening.add_argument("--url")
    opening.add_argument("--headed", action="store_false", dest="headless", default=True)
    opening.add_argument("--width", type=int, default=1280)
    opening.add_argument("--height", type=int, default=720)
    opening.add_argument("--browser-executable")
    session_commands.add_parser("list")
    closing = session_commands.add_parser("close")
    closing.add_argument("--session", dest="session_id", required=True)

    observe = commands.add_parser("observe")
    observe.add_argument("--session", dest="session_id", required=True)
    observe.add_argument("--no-ocr", action="store_false", dest="ocr", default=True)

    act = commands.add_parser("act")
    act.add_argument("--session", dest="session_id", required=True)
    act.add_argument("--observation", dest="observation_id", required=True)
    act.add_argument("--type", choices=["click", "swipe", "key", "text"], default="click")
    target = act.add_mutually_exclusive_group()
    target.add_argument("--target")
    target.add_argument("--box", type=_box)
    act.add_argument("--x2", type=int)
    act.add_argument("--y2", type=int)
    act.add_argument("--duration", type=int, default=300)
    act.add_argument("--key", type=int)
    act.add_argument("--text")

    probe = commands.add_parser("probe")
    probe.add_argument("--session", dest="session_id", required=True)
    probe.add_argument("--observation", dest="observation_id", required=True)
    probe.add_argument("--node", required=True)
    probe.add_argument("--override", type=_json_object, default={})

    run = commands.add_parser("run")
    run.add_argument("--session", dest="session_id", required=True)
    run.add_argument("--entry", required=True)
    run.add_argument("--override", type=_json_object, default={})
    run.add_argument("--timeout-seconds", type=float, default=20)
    run.add_argument("--goal", help="Recognition-only node used to independently check the goal.")
    run.add_argument(
        "--full",
        action="store_true",
        help="Return the complete event report instead of the compact summary.",
    )

    assets = commands.add_parser("asset")
    asset_commands = assets.add_subparsers(dest="operation", required=True)
    extract = asset_commands.add_parser("extract")
    extract.add_argument("--observation", dest="observation_id", required=True)
    extract.add_argument("--box", type=_box, required=True)
    extract.add_argument("--name", required=True)

    cases = commands.add_parser("case")
    case_commands = cases.add_subparsers(dest="operation", required=True)
    check = case_commands.add_parser("check")
    check.add_argument("--session", dest="session_id", required=True)
    check.add_argument("--cases", required=True)

    artifact = commands.add_parser("artifact")
    artifact.add_argument(
        "--kind", choices=["observations", "actions", "probes", "runs"], required=True
    )
    artifact.add_argument("--id", required=True)

    doctor = commands.add_parser("doctor", help="Check local project and optional adapters.")
    doctor.add_argument("--adb-path", default="adb")
    doctor.add_argument("--resource-path", default="resource")
    devices = commands.add_parser("devices", help="Discover available ADB devices.")
    devices.add_argument("--adb-path", default="adb")
    browser = commands.add_parser("browser", help="Install Chromium into this package's runtime.")
    browser.add_argument("operation", choices=["install"])
    target_app = commands.add_parser("target", help="Prepare a pinned open-source UI target.")
    target_app.add_argument("operation", choices=["prepare", "serve"])
    target_app.add_argument("name", choices=["drawio"])
    target_app.add_argument("--cache", type=Path)
    target_app.add_argument("--port", type=int, default=8765)
    demo = commands.add_parser("demo")
    demo.add_argument("operation", choices=["init"])
    demo.add_argument("--starter", action="store_true")
    agent = commands.add_parser("agent")
    agent_commands = agent.add_subparsers(dest="operation", required=True)
    agent_commands.add_parser("show")
    install = agent_commands.add_parser("install")
    install.add_argument("--target", choices=["codex", "claude"], required=True)
    export_skill = agent_commands.add_parser("export")
    export_skill.add_argument("--output", required=True)
    tasks = commands.add_parser("task")
    task_commands = tasks.add_subparsers(dest="operation", required=True)
    create = task_commands.add_parser("create")
    create.add_argument("name")
    create.add_argument("--objective", required=True)
    create.add_argument("--entry", required=True)
    create.add_argument("--goal", required=True)
    create.add_argument("--cases")
    create.add_argument("--constraint", action="append", dest="constraints", default=[])
    create.add_argument("--max-attempts", type=int, default=10)
    create.add_argument(
        "--scenario", action="append", dest="scenarios", default=[], metavar="NAME=START_NODE"
    )
    task_commands.add_parser("list")
    context = task_commands.add_parser("context")
    context.add_argument("name")
    task_run = task_commands.add_parser("run")
    task_run.add_argument("name")
    task_run.add_argument("--session", dest="session_id", required=True)
    task_run.add_argument("--timeout-seconds", type=float, default=20)
    task_run.add_argument("--scenario")
    reports = commands.add_parser("report")
    report_commands = reports.add_subparsers(dest="operation", required=True)
    listing = report_commands.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    showing = report_commands.add_parser("show")
    showing.add_argument("run_id")
    export_report = report_commands.add_parser("export")
    export_report.add_argument("run_id")
    export_report.add_argument("--output", required=True)
    export_project = commands.add_parser("export")
    export_project.add_argument("--output", required=True)
    return parser


def initialize(project: Path) -> dict[str, Any]:
    project.mkdir(parents=True, exist_ok=True)
    for relative in ["resource/pipeline", "resource/image", "dev/tasks", "dev/cases"]:
        (project / relative).mkdir(parents=True, exist_ok=True)
    interface = {
        "interface_version": 2,
        "name": "vibe-maa-project",
        "controller": [{"name": "Android", "type": "Adb"}],
        "resource": [{"name": "Default", "path": ["./resource"]}],
        "task": [],
    }
    created = []
    try:
        with (project / "interface.json").open("x", encoding="utf-8") as stream:
            json.dump(interface, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        created.append("interface.json")
    except FileExistsError:
        pass
    ignore = project / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if ".maa-dev/" not in existing.splitlines():
        with ignore.open("a", encoding="utf-8") as stream:
            stream.write(("\n" if existing and not existing.endswith("\n") else "") + ".maa-dev/\n")
    return {"project": str(project), "created": created}


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = vars(_parser().parse_args(argv))
        project = arguments.pop("project").resolve()
        command = arguments.pop("command")
        if command == "init":
            response = envelope(initialize(project))
        elif command == "capabilities":
            from .capabilities import show

            response = envelope(show(**arguments))
        elif command == "context":
            from .context import show

            response = envelope(show(project, **arguments))
        elif command == "reference":
            from .reference import show

            response = envelope(show(**arguments))
        elif command == "doctor":
            from .diagnostics import doctor

            response = envelope(doctor(project, **arguments))
        elif command == "devices":
            from .diagnostics import devices

            response = envelope(devices(**arguments))
        elif command == "browser":
            import importlib.util
            import subprocess

            if importlib.util.find_spec("playwright") is None:
                raise DevError("BROWSER_UNAVAILABLE", "Install the vibe-maa[browser] extra first")
            completed = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                stdout=sys.stderr,
                stderr=sys.stderr,
            )
            if completed.returncode:
                raise DevError("BROWSER_INSTALL_FAILED", "Chromium installation failed; see stderr")
            response = envelope({"installed": "chromium"})
        elif command == "target":
            from .targets.drawio.prepare import manifest, provision

            cache = arguments.pop("cache") or project / "work/targets"
            operation = arguments.pop("operation")
            arguments.pop("name")
            port = arguments.pop("port")
            if not 0 <= port <= 65535:
                raise DevError("INVALID_ARGUMENT", "Port must be in [0,65535]")
            webroot = provision(cache)
            if operation == "prepare":
                response = envelope({"webroot": str(webroot), "target": manifest()})
            else:
                from functools import partial
                from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

                handler = partial(SimpleHTTPRequestHandler, directory=str(webroot))
                with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
                    print(
                        json.dumps(
                            envelope(
                                {
                                    "url": f"http://127.0.0.1:{server.server_port}"
                                    + manifest()["launch_path"],
                                    "target": manifest(),
                                }
                            )
                        ),
                        flush=True,
                    )
                    try:
                        server.serve_forever()
                    except KeyboardInterrupt:
                        pass
                return 0
        elif command == "demo":
            from .bootstrap import initialize_demo

            arguments.pop("operation")
            response = envelope(initialize_demo(project, **arguments))
        elif command == "agent":
            from .bootstrap import skill_export, skill_install, skill_show

            operation = arguments.pop("operation")
            result = (
                skill_show()
                if operation == "show"
                else (
                    skill_install(project, **arguments)
                    if operation == "install"
                    else skill_export(project, **arguments)
                )
            )
            response = envelope(result)
        elif command == "task" and arguments["operation"] != "run":
            from .tasks import TaskManager

            operation = arguments.pop("operation")
            if operation == "create":
                values = arguments.pop("scenarios")
                if values:
                    if any("=" not in value for value in values):
                        raise DevError("INVALID_ARGUMENT", "Scenarios use NAME=START_NODE")
                    scenarios = dict(value.split("=", 1) for value in values)
                    if len(scenarios) != len(values):
                        raise DevError("INVALID_ARGUMENT", "Scenario names must be unique")
                    arguments["scenarios"] = scenarios
            with project_lock(project / ".maa-dev/task-edit.lock"):
                response = envelope(getattr(TaskManager(project), operation)(**arguments))
        elif command in {"report", "export"}:
            from .reports import Reports

            reports = Reports(project)
            if command == "export":
                result = reports.export_project(**arguments)
            else:
                operation = arguments.pop("operation")
                method = {
                    "list": reports.list_runs,
                    "show": reports.summary,
                    "export": reports.export_run,
                }[operation]
                result = method(**arguments)
            response = envelope(result)
        elif command == "daemon":
            operation = arguments.pop("operation")
            response = (
                envelope(daemon_status(project)) if operation == "status" else stop_daemon(project)
            )
        else:
            full = arguments.pop("full", False)
            if command in {"session", "asset", "case", "task"}:
                method = f"{command}.{arguments.pop('operation')}"
            elif command == "artifact":
                method = "artifact.read"
            else:
                method = command
            params = {key: value for key, value in arguments.items() if value is not None}
            response = request(project, method, params)
            if command == "run" and response.get("ok") and not full:
                from .reports import Reports

                response = envelope(Reports(project).summary(response["result"]["run_id"]))
    except DevError as exc:
        response = envelope(error=exc)
    except (OSError, ValueError) as exc:
        response = envelope(error=DevError("CLIENT_ERROR", str(exc)))
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
