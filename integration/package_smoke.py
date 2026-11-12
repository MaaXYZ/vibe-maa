"""Exercise a built distribution from a workspace outside the source checkout."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import vibe_maa


def cli(project: Path, *arguments: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "vibe_maa", "--project", str(project), *arguments],
        cwd=project.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise AssertionError(f"CLI failed: {arguments}\n{result.stdout}\n{result.stderr}")
    response = json.loads(result.stdout)
    assert response["schema_version"] == 1 and response["ok"] is True, response
    return response["result"]


def verify_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for item in manifest["files"]:
            assert hashlib.sha256(archive.read(item["path"])).hexdigest() == item["sha256"]
        return manifest


def main() -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    installed = Path(vibe_maa.__file__).resolve()
    assert not installed.is_relative_to(source), f"Expected installed wheel, got {installed}"
    with tempfile.TemporaryDirectory(prefix="vibe-maa-wheel-") as temporary:
        project = Path(temporary) / "project"
        project.mkdir()
        cli(project, "demo", "init")
        cli(project, "agent", "install", "--target", "codex")
        assert (project / ".agents/skills/vibe-maa/SKILL.md").is_file()
        cli(
            project,
            "task",
            "create",
            "package-smoke",
            "--objective",
            "Open the mailbox and return home",
            "--entry",
            "OpenMailbox",
            "--goal",
            "HomeVisible",
            "--cases",
            "dev/cases/mailbox.json",
        )
        run_id = None
        try:
            opened = cli(
                project, "session", "open", "--backend", "demo", "--initial-state", "popup"
            )
            sid = opened["session_id"]
            observation = cli(project, "observe", "--session", sid)
            probe = cli(
                project,
                "probe",
                "--session",
                sid,
                "--observation",
                observation["observation_id"],
                "--node",
                "HomeVisible",
            )
            assert probe["result"]["hit"] is False
            task = cli(project, "task", "run", "package-smoke", "--session", sid)
            assert task["attempt"]["accepted"] is True, task
            assert task["context"]["state"] == "accepted"
            assert task["context"]["scope"] == "fixture"
            run_id = task["attempt"]["run_id"]
            summary = cli(project, "report", "show", run_id)
            assert summary["execution_status"] == "succeeded"
            assert summary["goal_status"] == "passed"
            cli(project, "report", "export", run_id, "--output", "evidence.zip")
            cli(project, "export", "--output", "project.zip")
            assert verify_archive(project / "evidence.zip")["scope"] == "fixture"
            assert verify_archive(project / "project.zip")["kind"] == "maa_project"
        finally:
            cli(project, "daemon", "stop")
        assert run_id is not None
        context = cli(project, "task", "context", "package-smoke")
        assert context["state"] == "accepted"
        assert cli(project, "report", "show", run_id)["scope"] == "fixture"
        assert cli(project, "daemon", "status")["running"] is False
        print(
            json.dumps(
                {
                    "installed_from": str(installed),
                    "version": vibe_maa.__version__,
                    "task_state": context["state"],
                    "scope": context["scope"],
                    "checks": "installed fixture, Skill, task, resume, reports, exports",
                }
            )
        )


if __name__ == "__main__":
    main()
