"""Run a pinned open-source coding agent, then independently grade its MaaFW deliverable."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from activity import ActivityTrace
from grader import ENTRY, drawio_server, grade_drawio, grade_mailbox, write_result

from vibe_maa.bootstrap import initialize_demo, skill_install
from vibe_maa.cli import initialize
from vibe_maa.daemon import stop_daemon
from vibe_maa.reports import Reports

ROOT = Path(__file__).resolve().parents[2]
HARNESS_VERSION = "0.167.2"


def redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def verify_publication(directory: Path, secret: str | None) -> None:
    if not secret:
        return
    marker = secret.encode()
    for path in directory.iterdir():
        matched = False
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    with archive.open(member) as stream:
                        tail = b""
                        while chunk := stream.read(65536):
                            combined = tail + chunk
                            if marker in combined:
                                matched = True
                                break
                            tail = combined[-len(marker) :]
                    if matched:
                        break
        else:
            matched = marker in path.read_bytes()
        if matched:
            path.unlink()
            raise ValueError(
                "Publication rejected because a generated artifact contained the API key"
            )


def configuration(local_auth: bool, reasoning_effort: str = "configured") -> dict[str, Any]:
    if reasoning_effort not in {"configured", "low", "high"}:
        raise ValueError("reasoning-effort must be configured, low, or high")
    explicit_effort = reasoning_effort != "configured"
    if local_auth:
        return {
            "authentication": "existing_local_codex_login",
            "reasoning_effort_requested": reasoning_effort
            if explicit_effort
            else "local_harness_default",
            "configuration_source": {
                "authentication": "existing_local_codex_login",
                "model": "local_codex_configuration",
                "base_url": "local_codex_configuration",
                "reasoning_effort": "run_override"
                if explicit_effort
                else "local_codex_configuration",
            },
        }
    key = os.environ.get("VIBE_MAA_LLM_API_KEY", "")
    model = os.environ.get("VIBE_MAA_LLM_MODEL", "")
    base = os.environ.get("VIBE_MAA_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    if not key:
        raise ValueError("Set GitHub Secret VIBE_MAA_LLM_API_KEY")
    if not model:
        raise ValueError(
            "Set repository Secret or Variable VIBE_MAA_LLM_MODEL to a vision/tool-capable model"
        )
    try:
        parsed = urlsplit(base)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("VIBE_MAA_LLM_BASE_URL must contain a valid host and port") from exc
    if not parsed.hostname or not (
        parsed.scheme == "https"
        or (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and port is not None
        )
    ):
        raise ValueError("VIBE_MAA_LLM_BASE_URL must be HTTPS or an explicit loopback endpoint")
    if base.endswith("/responses") or base.endswith("/chat/completions"):
        raise ValueError(
            "VIBE_MAA_LLM_BASE_URL is the API base, for example https://api.openai.com/v1"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Put API credentials in the secret, not in VIBE_MAA_LLM_BASE_URL")
    configured_effort = os.environ.get("VIBE_MAA_LLM_REASONING_EFFORT", "").strip()
    return {
        "model": model,
        "base_url": base,
        "authentication": "api_key",
        "wire_api": "responses",
        "reasoning_effort_requested": reasoning_effort
        if explicit_effort
        else configured_effort or "provider_default",
        "configuration_source": {
            "authentication": "api_key_environment",
            "model": "environment",
            "base_url": "environment" if "VIBE_MAA_LLM_BASE_URL" in os.environ else "default",
            "reasoning_effort": "run_override"
            if explicit_effort
            else "environment"
            if configured_effort
            else "provider_default",
        },
    }


def harness(local_auth: bool) -> str:
    binary = (
        ROOT / "integration/agent/node_modules/.bin" / ("codex.cmd" if os.name == "nt" else "codex")
    )
    if binary.is_file():
        return str(binary)
    if local_auth:
        installed = shutil.which("codex")
        if installed:
            return installed
    raise ValueError("Install the pinned harness: npm ci --prefix integration/agent")


def prompt_for(target: str, project: Path, url: str | None) -> str:
    task = (
        "Repair the starter so it opens the mailbox and returns home from home, mailbox, and "
        "announcement popup starting states. The starter intentionally lacks popup handling. "
        "Create an AgentTask entry that accomplishes this. Do not change fixture code or case labels. "
        "Open demo sessions with explicit starting states to explore."
        if target == "mailbox"
        else "Create a rectangle shape in the real diagrams.net application canvas and label it exactly "
        "Vibe Maa. Deliver a standard MaaFramework Pipeline with entry AgentTask that repeats this "
        "from a fresh blank document. Use the browser backend at this URL: " + str(url)
    )
    return (
        "You are developing a standard MaaFramework automation using the installed vibe-maa tool.\n"
        f"Your writable project is {project}.\n{task}\n\n"
        "Read .agents/skills/vibe-maa/SKILL.md and use vibe-maa --help. "
        "Use only vibe-maa observations/screenshots and input operations for application exploration; "
        "inspect saved PNGs with your image viewing tool. Do not use DOM, accessibility, Playwright "
        "directly, application internals, or hidden grader information to implement the automation. "
        "You may use native Click, LongPress, Swipe, ClickKey, InputText, and DoNothing actions, plus "
        "built-in recognition. Shell/Command/Custom actions are not allowed in the deliverable. "
        "Edit ordinary files under resource/pipeline and resource/image. "
        "Expose the AgentTask entry in interface.json's task list for a normal MaaFW handoff. "
        f"The independent grader will start new sessions and run {ENTRY}; completion claims are "
        "not acceptance. Keep the source files and explain what you actually tested. "
        "Do not modify the installed tool, the upstream application, or external test harness. "
        "Close your sessions/daemon when finished. Work within the provided time budget."
    )


def execute(
    binary: str,
    project: Path,
    prompt: str,
    config: dict[str, Any],
    seconds: int,
    private: Path,
    local_auth: bool,
) -> dict[str, Any]:
    command = [
        binary,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-c",
        "sandbox_workspace_write.network_access=true",
        "--json",
        "-C",
        str(project),
        "-c",
        'shell_environment_policy.exclude=["VIBE_MAA_LLM_API_KEY","OPENAI_API_KEY","CODEX_API_KEY","GH_TOKEN","GITHUB_TOKEN"]',
    ]
    environment = os.environ.copy()
    for name in ["GH_TOKEN", "GITHUB_TOKEN"]:
        environment.pop(name, None)
    secret = environment.get("VIBE_MAA_LLM_API_KEY")
    if not local_auth:
        home = private / "codex-home"
        home.mkdir()
        environment["CODEX_HOME"] = str(home)
        command += [
            "--ignore-user-config",
            "--model",
            config["model"],
            "-c",
            'model_provider="vibe_ci"',
            "-c",
            'model_providers.vibe_ci.name="Vibe Maa CI"',
            "-c",
            f"model_providers.vibe_ci.base_url={json.dumps(config['base_url'])}",
            "-c",
            'model_providers.vibe_ci.env_key="VIBE_MAA_LLM_API_KEY"',
            "-c",
            'model_providers.vibe_ci.wire_api="responses"',
        ]
    effort = config.get("reasoning_effort_requested")
    if effort and effort not in {"provider_default", "local_harness_default"}:
        command += ["-c", f"model_reasoning_effort={json.dumps(effort)}"]
    command.append(prompt)
    # The launcher keeps auth outside the writable project; no auth/log directory is published.
    raw_path = private / "agent-output.private.jsonl"
    timed_out = False
    activity = ActivityTrace(secret)
    started = time.monotonic()
    next_heartbeat = started + 30
    with raw_path.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )
        with raw_path.open("rb") as stream:
            while True:
                activity.read(stream)
                returncode = process.poll()
                if returncode is not None:
                    break
                current = time.monotonic()
                if current >= next_heartbeat:
                    print(json.dumps(activity.progress(current - started)), flush=True)
                    next_heartbeat = current + 30
                if current - started >= seconds:
                    timed_out = True
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        if os.name != "nt":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait()
                    returncode = process.returncode
                    break
                time.sleep(min(0.25, max(0.01, seconds - (current - started))))
            activity.read(stream, final=True)
    trace_path = private.parent / "published" / "agent-activity.json"
    write_result(trace_path, activity.result())
    print(json.dumps(activity.progress(time.monotonic() - started)), flush=True)
    raw = raw_path.read_text(encoding="utf-8", errors="replace")
    sanitized = redact(raw, secret)
    (private / "agent-output.redacted.jsonl").write_text(sanitized, encoding="utf-8")
    raw_path.unlink()
    failure = activity.terminal_error
    if returncode != 0 and failure is None and activity.errors:
        failure = activity.errors[-1]
    version = subprocess.run(
        [binary, "--version"], text=True, capture_output=True, check=True
    ).stdout.strip()
    completed_commands = {
        item.get("item_id", f"record_{index}"): item
        for index, item in enumerate(activity.events)
        if item.get("tool_type") == "command_execution" and item.get("event") == "item.completed"
    }
    return {
        "returncode": returncode,
        "timed_out": timed_out,
        "harness": "openai/codex",
        "harness_version": version,
        "wall_time_budget_seconds": seconds,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "budget_scope": "wall_time_only_not_a_currency_or_token_limit",
        "error": failure,
        "usage": dict(activity.usage) or None,
        "usage_last_turn": activity.usage_last_turn,
        "completed_turns": activity.completed_turns,
        "usage_measurement": "sum_of_reported_turn.completed_events",
        "terminal_state": activity.terminal_state,
        "activity_trace": trace_path.name,
        "historical_error_count": activity.event_types["error"]
        + activity.event_types["turn.failed"],
        "tool_items": len(activity.tool_ids),
        "failed_command_items": sum(
            type(item.get("exit_code")) is int and item["exit_code"] != 0
            for item in completed_commands.values()
        ),
        "command_failure_measurement": "retained_completed_command_items",
        "activity_truncated_records": activity.dropped,
    }


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    config = configuration(arguments.local_auth, arguments.reasoning_effort)
    binary = harness(arguments.local_auth)
    output = arguments.output.resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new path for each acceptance run")
    project, private, published = output / "project", output / "private", output / "published"
    private.mkdir(parents=True)
    published.mkdir()
    if arguments.target == "mailbox":
        initialize_demo(project, starter=True)
    else:
        initialize(project)
    skill_install(project, "codex")
    server = (
        drawio_server(arguments.cache.resolve())
        if arguments.target == "drawio"
        else contextlib.nullcontext((None, None))
    )
    try:
        with server as (url, upstream):
            prompt = prompt_for(arguments.target, project, url)
            (private / "prompt.txt").write_text(prompt, encoding="utf-8")
            agent = execute(
                binary,
                project,
                prompt,
                config,
                arguments.timeout_seconds,
                private,
                arguments.local_auth,
            )
            stop_daemon(project)
            # The grader is external to the writable project and does not trust the agent's verdict.
            try:
                grade = (
                    grade_mailbox(project)
                    if arguments.target == "mailbox"
                    else grade_drawio(project, url, arguments.browser_executable)
                )
            except Exception as exc:
                grade = {
                    "passed": False,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
            if (
                agent["returncode"] != 0
                or agent["timed_out"]
                or agent["error"]
                or agent["terminal_state"] != "completed"
            ):
                grade["passed"] = False
            result = {
                "schema_version": 1,
                "target": arguments.target,
                "agent": agent,
                "configuration": config,
                "source_revision": os.environ.get("GITHUB_SHA"),
                "upstream": upstream,
                "grade": grade,
            }
            try:
                Reports(project).export_project(str(published / "automation.zip"))
                for check in grade.get("checks", []):
                    if check.get("run_id"):
                        run_id = check["run_id"]
                        Reports(project).export_run(
                            run_id, str(published / f"evidence-{run_id}.zip")
                        )
            except Exception as exc:
                result["export_error"] = str(exc)
                grade["passed"] = False
            verify_publication(published, os.environ.get("VIBE_MAA_LLM_API_KEY"))
            write_result(published / "acceptance.json", result)
            verify_publication(published, os.environ.get("VIBE_MAA_LLM_API_KEY"))
            return result
    finally:
        with contextlib.suppress(Exception):
            stop_daemon(project)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["drawio", "mailbox"], default="drawio")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "work/upstream")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--local-auth",
        action="store_true",
        help="Developer experiment using an existing local Codex login; CI uses API secrets",
    )
    parser.add_argument("--browser-executable")
    parser.add_argument(
        "--reasoning-effort",
        choices=["configured", "low", "high"],
        default="configured",
        help="Override reasoning for this run only; configured uses the repository/local setting",
    )
    arguments = parser.parse_args()
    if not 30 <= arguments.timeout_seconds <= 900:
        parser.error("timeout-seconds must be in [30,900]")
    try:
        result = run(arguments)
    except Exception as exc:
        secret = os.environ.get("VIBE_MAA_LLM_API_KEY")
        result = {
            "schema_version": 1,
            "passed": False,
            "error": {"type": type(exc).__name__, "message": redact(str(exc), secret)},
        }
        destination = arguments.output.resolve() / "published/acceptance.json"
        if not destination.exists():
            write_result(destination, result)
        print(json.dumps(result))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["grade"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
