"""JSON command-line client for the persistent project-local development service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .daemon import daemon_status, envelope, request, stop_daemon
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

    daemon = commands.add_parser("daemon", help="Inspect or stop the project-local daemon.")
    daemon.add_argument("operation", choices=["status", "stop"])

    sessions = commands.add_parser("session")
    session_commands = sessions.add_subparsers(dest="operation", required=True)
    opening = session_commands.add_parser("open")
    opening.add_argument("--backend", choices=["demo", "adb"], default="demo")
    opening.add_argument("--device", default="")
    opening.add_argument("--adb-path", default="adb")
    opening.add_argument("--resource-path", default="resource")
    opening.add_argument("--initial-state", choices=["home", "mailbox", "popup"], default="home")
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
        elif command == "daemon":
            operation = arguments.pop("operation")
            response = (
                envelope(daemon_status(project)) if operation == "status" else stop_daemon(project)
            )
        else:
            if command in {"session", "asset", "case"}:
                method = f"{command}.{arguments.pop('operation')}"
            elif command == "artifact":
                method = "artifact.read"
            else:
                method = command
            params = {key: value for key, value in arguments.items() if value is not None}
            response = request(project, method, params)
    except DevError as exc:
        response = envelope(error=exc)
    except (OSError, ValueError) as exc:
        response = envelope(error=DevError("CLIENT_ERROR", str(exc)))
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
