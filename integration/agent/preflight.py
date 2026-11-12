"""Exercise the pinned Codex sandbox and host-owned native service without an LLM."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from isolation import PROFILE, profile_arguments

from vibe_maa.bootstrap import initialize_demo
from vibe_maa.daemon import daemon_status, stop_daemon

ROOT = Path(__file__).resolve().parents[2]
HARNESS_VERSION = "codex-cli 0.167.2"

# This subprocess only wraps the ordinary CLI and adds namespace evidence. Application
# interactions still go through the native service, never through app internals.
CLIENT = """
import json, os, subprocess, sys
result = subprocess.run(
    [sys.executable, '-m', 'vibe_maa', '--project', sys.argv[1], *sys.argv[2:]],
    text=True, capture_output=True, check=False,
)
print(json.dumps({
    'pid_namespace': os.readlink('/proc/self/ns/pid'),
    'returncode': result.returncode,
    'response': json.loads(result.stdout),
}))
"""

ISOLATION_CHECK = """
import json,sys
from pathlib import Path
project, root, private = map(Path, sys.argv[1:4])
targets = {
    'published_solution': root / 'examples/agent-generated/drawio/resource/pipeline/AgentTask.json',
    'grader': root / 'integration/agent/grader.py',
    'benchmarks': root / 'docs/benchmarks.md',
    'benchmark_record': Path(sys.argv[4]),
    'test_source': root / 'tests/test_demo.py',
    'git_history': root / '.git/config',
    'private_sentinel': private / 'private-sentinel.txt',
}
alias = project / 'forbidden-symlink'
alias.symlink_to(targets['published_solution'])
targets['solution_symlink'] = alias
checks = {}
for label,path in targets.items():
    try:
        path.read_bytes()
        checks[label] = False
    except PermissionError:
        checks[label] = True
alias.unlink()
from vibe_maa import __version__
print(json.dumps({'denied_reads':checks,'tool_import_version':__version__}))
if not all(checks.values()):
    raise SystemExit(1)
"""


def _environment() -> dict[str, str]:
    """Do not provide service credentials to this API-free check."""
    return {
        key: value
        for key, value in os.environ.items()
        if not any(part in key.upper() for part in ("TOKEN", "API_KEY", "SECRET", "PASSWORD"))
        and not key.startswith("VIBE_MAA_EXTERNAL_DAEMON_")
    }


def run_preflight(binary: str, work_root: Path | None = None) -> dict[str, Any]:
    started = time.monotonic()
    if sys.platform != "linux":
        raise RuntimeError("The pinned Codex PID namespace preflight requires Linux")
    binary = str(Path(binary).resolve(strict=True))
    environment = _environment()
    version = subprocess.run(
        [binary, "--version"],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    ).stdout.strip()
    if version != HARNESS_VERSION:
        raise RuntimeError(f"Expected {HARNESS_VERSION}, received {version}")
    work_root = work_root or ROOT / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vibe-codex-preflight-", dir=work_root) as directory:
        temporary = Path(directory).resolve()
        project = temporary / "project"
        initialize_demo(project)
        # An empty Codex home prevents loading local provider credentials/configuration.
        codex_home = temporary / "codex-home"
        codex_home.mkdir()
        (temporary / "private-sentinel.txt").write_text(
            "preflight-read-denial-sentinel", encoding="utf-8"
        )
        environment["CODEX_HOME"] = str(codex_home)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        host_namespace = os.readlink("/proc/self/ns/pid")
        records: list[dict[str, Any]] = []
        with (temporary / "host-service.log").open("w", encoding="utf-8") as log:
            owner = subprocess.Popen(
                [sys.executable, "-m", "vibe_maa", "--project", str(project), "daemon", "serve"],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 15
                status: dict[str, Any] = {}
                while time.monotonic() < deadline:
                    status = daemon_status(project)
                    if status.get("running") and status.get("compatible"):
                        break
                    if owner.poll() is not None:
                        raise RuntimeError("Host service exited during startup")
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Host service did not become ready")
                expected_pid = status["pid"]
                environment["VIBE_MAA_EXTERNAL_DAEMON_PROJECT"] = str(project)
                environment["VIBE_MAA_EXTERNAL_DAEMON_PID"] = str(expected_pid)
                permission_arguments = profile_arguments(
                    project,
                    select=False,
                    protected=(
                        codex_home,
                        temporary / "private-sentinel.txt",
                        temporary / "host-service.log",
                    ),
                )
                command = [
                    binary,
                    "sandbox",
                    "-P",
                    PROFILE,
                    *permission_arguments,
                    "-C",
                    str(project),
                    "--",
                    sys.executable,
                    "-c",
                    CLIENT,
                    str(project),
                ]

                def invoke(*arguments: str) -> dict[str, Any]:
                    completed = subprocess.run(
                        [*command, *arguments],
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=45,
                    )
                    if completed.returncode:
                        raise RuntimeError(
                            f"Pinned Codex sandbox failed before CLI response ({completed.returncode}): "
                            + completed.stderr.strip()[:1000]
                        )
                    result = json.loads(completed.stdout)
                    if result["pid_namespace"] == host_namespace:
                        raise RuntimeError("Codex command did not enter a separate PID namespace")
                    live = daemon_status(project)
                    if not live.get("running") or live.get("pid") != expected_pid:
                        raise RuntimeError("Host service owner changed between sandbox commands")
                    records.append(
                        {
                            "operation": " ".join(arguments[:2]),
                            "separate_pid_namespace": True,
                            "same_host_owner": True,
                            "returncode": result["returncode"],
                            "ok": result["response"].get("ok"),
                        }
                    )
                    return result

                opened = invoke("session", "open", "--backend", "demo")
                if opened["returncode"] or not opened["response"].get("ok"):
                    raise RuntimeError("Native demo session did not open inside Codex sandbox")
                session = opened["response"]["result"]["session_id"]
                observed = invoke("observe", "--session", session, "--no-ocr")
                if observed["returncode"] or not observed["response"].get("ok"):
                    raise RuntimeError(
                        "Native demo session did not survive the next sandbox command"
                    )
                observation = observed["response"]["result"]
                if not observation.get("observation_id"):
                    raise RuntimeError("Native observation did not provide evidence")
                stopped = invoke("daemon", "stop")
                if (
                    stopped["returncode"] == 0
                    or stopped["response"].get("error", {}).get("code") != "EXTERNAL_DAEMON_MANAGED"
                ):
                    raise RuntimeError("A sandbox client could stop the supervisor-owned service")
                closed = invoke("session", "close", "--session", session)
                if closed["returncode"] or not closed["response"].get("ok"):
                    raise RuntimeError("Sandbox client could not close its session")
                isolated = subprocess.run(
                    [
                        binary,
                        "sandbox",
                        "-P",
                        PROFILE,
                        *permission_arguments,
                        "-C",
                        str(project),
                        "--",
                        sys.executable,
                        "-c",
                        ISOLATION_CHECK,
                        str(project),
                        str(ROOT),
                        str(temporary),
                        str(next((ROOT / "docs/records").glob("*.json"))),
                    ],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
                if isolated.returncode:
                    raise RuntimeError(
                        "Checkout read isolation failed: " + isolated.stderr.strip()[:1000]
                    )
                isolation = json.loads(isolated.stdout)
            finally:
                # The supervisor, outside all command namespaces, owns shutdown.
                try:
                    stop_daemon(project)
                finally:
                    try:
                        owner.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        owner.terminate()
                        try:
                            owner.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            owner.kill()
                            owner.wait(timeout=5)
        return {
            "schema_version": 1,
            "passed": True,
            "scope": "pinned_codex_sandbox_native_demo_no_model_api",
            "harness_version": version,
            "separate_sandbox_invocations": len(records),
            "session_reused_across_commands": True,
            "managed_stop_denied": True,
            "source_isolation": {"scope": "checkout_runtime_allowlist", **isolation},
            "checks": records,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", default=str(ROOT / "integration" / "agent" / "node_modules" / ".bin" / "codex")
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        result = run_preflight(arguments.codex)
    except Exception as exc:
        result = {
            "schema_version": 1,
            "passed": False,
            "scope": "pinned_codex_sandbox_native_demo_no_model_api",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
