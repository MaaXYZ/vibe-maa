"""Installed-package examples and project-local coding-agent integration."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import __version__
from .errors import DevError


def _resource_bytes(relative: str) -> bytes:
    return files("vibe_maa").joinpath("data", *relative.split("/")).read_bytes()


def _inside(project: Path, relative: str) -> Path:
    path = (project / relative).resolve()
    if not path.is_relative_to(project):
        raise DevError("INVALID_PATH", "Destination must remain inside the project")
    return path


def _write_new_files(project: Path, contents: dict[str, bytes]) -> list[str]:
    targets = {relative: _inside(project, relative) for relative in contents}
    conflicts = [relative for relative, path in targets.items() if path.exists()]
    if conflicts:
        raise DevError(
            "ALREADY_EXISTS", "Initialization would overwrite existing files", {"files": conflicts}
        )
    created: list[Path] = []
    try:
        for relative, path in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                created.append(path)
                stream.write(contents[relative])
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return list(targets)


def initialize_demo(project: Path, starter: bool = False) -> dict[str, Any]:
    """Create a self-contained fixture project from resources included in the wheel."""
    project = Path(project).resolve()
    ignore = _inside(project, ".gitignore")
    pipeline = json.loads(_resource_bytes("mailbox/resource/pipeline/mailbox.json"))
    if starter:
        del pipeline["DismissAnnouncement"]
        pipeline["OpenMailbox"]["next"].remove("DismissAnnouncement")
    interface = {
        "interface_version": 2,
        "name": "vibe-maa-mailbox",
        "label": "Vibe Maa mailbox fixture",
        "controller": [{"name": "Android", "type": "Adb"}],
        "resource": [{"name": "Default", "path": ["./resource"]}],
        "task": [
            {"name": "OpenMailbox", "label": "Open mailbox and return home", "entry": "OpenMailbox"}
        ],
    }
    contents = {
        "interface.json": (json.dumps(interface, indent=2) + "\n").encode(),
        "resource/pipeline/mailbox.json": (json.dumps(pipeline, indent=2) + "\n").encode(),
    }
    for name in ["home.png", "mailbox.png", "popup.png", "mailbox.json"]:
        contents[f"dev/cases/{name}"] = _resource_bytes(f"mailbox/dev/cases/{name}")
    contents["DEMO.md"] = (
        "# Resettable mailbox fixture\n\n"
        "This project uses the real MaaFramework runtime over a deterministic fixture UI.\n"
        "Open a session with `--backend demo`; fixture results do not verify a real game.\n\n"
        "Task: open the mailbox and return home from home, mailbox, or announcement popup.\n"
        "Entry: `OpenMailbox`. Independent completion check: `HomeVisible`.\n"
        "Offline cases: `dev/cases/mailbox.json`.\n\n"
        + (
            "This starter intentionally has no announcement handling. Explore the popup, edit\n"
            "`resource/pipeline/mailbox.json`, and validate the edited source. Do not change the\n"
            "completion condition or case labels just to make validation pass.\n"
            if starter
            else "The included pipeline already handles all three starting states.\n"
        )
    ).encode()
    if not ignore.exists():
        contents[".gitignore"] = b".maa-dev/\n"
    created = _write_new_files(project, contents)
    existing_ignore = ignore.read_text(encoding="utf-8")
    if ".maa-dev/" not in existing_ignore.splitlines():
        with ignore.open("a", encoding="utf-8") as stream:
            stream.write(
                ("\n" if existing_ignore and not existing_ignore.endswith("\n") else "")
                + ".maa-dev/\n"
            )
    (project / "resource" / "image").mkdir(parents=True, exist_ok=True)
    (project / "dev" / "tasks").mkdir(parents=True, exist_ok=True)
    return {
        "project": str(project),
        "demo": "mailbox",
        "scope": "fixture",
        "starter": starter,
        "created": created,
        "entry": "OpenMailbox",
        "goal": "HomeVisible",
        "cases": "dev/cases/mailbox.json",
        "next_command": "session open --backend demo --initial-state popup",
        "suggested_task": {
            "name": "mailbox",
            "objective": "Open the mailbox and return home, including an announcement popup",
            "entry": "OpenMailbox",
            "goal": "HomeVisible",
            "cases": "dev/cases/mailbox.json",
            "scenarios": {
                "home": "HomeVisible",
                "mailbox": "MailboxVisible",
                "popup": "PopupVisible",
            },
        },
    }


def skill_show() -> dict[str, Any]:
    content = _resource_bytes("skill/SKILL.md")
    return {
        "name": "vibe-maa",
        "version": __version__,
        "sha256": hashlib.sha256(content).hexdigest(),
        "content": content.decode(),
    }


def skill_export(project: Path, output: str = "vibe-maa-skill") -> dict[str, Any]:
    project = Path(project).resolve()
    destination = _inside(project, output)
    if destination.exists():
        raise DevError("ALREADY_EXISTS", "Skill destination already exists")
    relative = str(destination.relative_to(project) / "SKILL.md")
    content = _resource_bytes("skill/SKILL.md")
    _write_new_files(project, {relative: content})
    return {
        "name": "vibe-maa",
        "version": __version__,
        "directory": str(destination),
        "skill_path": str(destination / "SKILL.md"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "scope": "project",
    }


def skill_install(project: Path, target: str) -> dict[str, Any]:
    destinations = {
        "codex": ".agents/skills/vibe-maa",
        "claude": ".claude/skills/vibe-maa",
    }
    if target not in destinations:
        raise DevError("INVALID_ARGUMENT", "Agent target must be codex or claude")
    result = skill_export(project, destinations[target])
    return {**result, "target": target, "next_action": "Start or refresh the coding-agent session"}
