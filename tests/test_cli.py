from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vibe_maa import cli, daemon
from vibe_maa.errors import DevError

FAKE_DAEMON = """
from pathlib import Path
import sys
import time
from vibe_maa.daemon import run_daemon
from vibe_maa.errors import DevError

class Service:
    def __init__(self, project):
        self.project = project
        self.counter = 0
    def dispatch(self, method, params):
        if method == 'broken':
            raise DevError('EXPECTED_ERROR', 'Expected diagnostic failure.')
        value = self.counter
        time.sleep(params.get('delay', 0))
        self.counter = value + 1
        return {'counter': self.counter, 'method': method, 'params': params}
    def close(self):
        (self.project / 'closed').write_text('closed')

run_daemon(Path(sys.argv[1]), service_factory=Service)
"""


@pytest.fixture
def running_daemon(tmp_path):
    log = (tmp_path / "daemon-test.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-c", FAKE_DAEMON, str(tmp_path)], stdout=log, stderr=log
    )
    deadline = time.monotonic() + 10
    while not daemon.daemon_status(tmp_path)["running"]:
        if process.poll() is not None or time.monotonic() > deadline:
            log.close()
            pytest.fail((tmp_path / "daemon-test.log").read_text())
        time.sleep(0.02)
    yield tmp_path, process
    if process.poll() is None:
        daemon.stop_daemon(tmp_path)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    log.close()


def invoke(project: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "vibe_maa", "--project", str(project), *arguments],
        text=True,
        capture_output=True,
        timeout=15,
    )


def test_independent_cli_processes_share_daemon_state(running_daemon):
    project, process = running_daemon
    first = invoke(project, "session", "open", "--backend", "demo")
    second = invoke(project, "observe", "--session", "session-one")
    assert first.returncode == second.returncode == 0
    one, two = json.loads(first.stdout), json.loads(second.stdout)
    assert one["result"]["counter"] == 1
    assert two["result"]["counter"] == 2
    assert two["result"]["params"] == {"session_id": "session-one", "ocr": True}
    status = json.loads(invoke(project, "daemon", "status").stdout)
    assert status["result"]["pid"] == process.pid
    assert "token" not in status["result"]


def test_daemon_serializes_mutations_but_health_stays_responsive(running_daemon):
    project, _ = running_daemon
    state = daemon._read_state(project)
    with ThreadPoolExecutor(max_workers=6) as workers:
        jobs = [
            workers.submit(daemon._exchange, state, "counter", {"delay": 0.03}, 5) for _ in range(6)
        ]
        assert daemon.daemon_status(project)["running"]
        counters = sorted(job.result()["result"]["counter"] for job in jobs)
    assert counters == list(range(1, 7))


def test_authentication_errors_do_not_dispatch(running_daemon):
    project, _ = running_daemon
    state = daemon._read_state(project)
    denied = daemon._exchange({**state, "token": "incorrect"}, "counter", {}, 5)
    assert denied["error"]["code"] == "AUTH_FAILED"
    accepted = daemon._exchange(state, "counter", {}, 5)
    assert accepted["result"]["counter"] == 1
    if os.name != "nt":
        assert daemon._state_path(project).stat().st_mode & 0o777 == 0o600


def test_stop_closes_service_and_removes_metadata(running_daemon):
    project, process = running_daemon
    assert daemon.stop_daemon(project)["result"]["stopped"] is True
    process.wait(timeout=10)
    assert (project / "closed").read_text() == "closed"
    assert not daemon._state_path(project).exists()
    assert not daemon.daemon_status(project)["running"]


def test_stop_rejects_accepted_queued_mutation(tmp_path):
    """A stop request permits the active operation to finish, but never starts queued input."""
    entered, release, queued = threading.Event(), threading.Event(), threading.Event()

    class ObservedLock:
        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if self.lock.locked():
                queued.set()
            self.lock.acquire()

        def __exit__(self, *_):
            self.lock.release()

    class BlockingService:
        def __init__(self):
            self.dispatched = []

        def dispatch(self, method, params):
            self.dispatched.append(method)
            entered.set()
            assert release.wait(5)
            return {"completed": method}

    service = BlockingService()
    server = daemon._Server(service, "test-token", tmp_path)
    server.operation_lock = ObservedLock()
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    state = {"token": "test-token", "port": server.server_address[1]}
    try:
        with ThreadPoolExecutor(max_workers=2) as clients:
            active = clients.submit(daemon._exchange, state, "active", {}, 5)
            assert entered.wait(2)
            pending = clients.submit(daemon._exchange, state, "queued", {}, 5)
            assert queued.wait(2)
            stopped = daemon._exchange(state, "daemon.stop", {}, 5)
            assert stopped["result"]["stopped"]
            assert server.stopping.is_set()
            release.set()
            assert active.result()["ok"] is True
            assert pending.result()["error"]["code"] == "DAEMON_STOPPING"
        assert service.dispatched == ["active"]
    finally:
        release.set()
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def test_single_owner_rejects_second_daemon(running_daemon):
    project, process = running_daemon
    second = subprocess.run(
        [sys.executable, "-c", FAKE_DAEMON, str(project)], capture_output=True, timeout=5
    )
    assert second.returncode != 0
    assert daemon.daemon_status(project)["pid"] == process.pid


def test_copied_metadata_cannot_attach_to_another_project(running_daemon, tmp_path):
    project, _ = running_daemon
    other = tmp_path / "other-project"
    (other / ".maa-dev").mkdir(parents=True)
    daemon._write_state(other, daemon._read_state(project))
    assert daemon.daemon_status(other) == {"running": False, "stale_metadata": True}


def test_lost_mutation_response_is_not_retried(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(daemon, "ensure_daemon", lambda _: {"port": 1, "token": "token"})

    def exchange(*args):
        calls.append(args)
        raise DevError("DAEMON_UNAVAILABLE", "Connection lost after dispatch.")

    monkeypatch.setattr(daemon, "_exchange", exchange)
    with pytest.raises(DevError, match="Connection lost"):
        daemon.request(tmp_path, "act", {"type": "click"})
    assert len(calls) == 1


def test_cli_errors_are_machine_readable(capsys):
    assert cli.main(["act", "--session", "s"]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["schema_version"] == 1
    assert error["ok"] is False
    assert error["error"]["code"] == "INVALID_ARGUMENT"


def test_project_init_preserves_existing_files(tmp_path, capsys):
    interface = tmp_path / "interface.json"
    interface.write_text('{"project": "existing"}')
    assert cli.main(["--project", str(tmp_path), "init"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ok"]
    assert interface.read_text() == '{"project": "existing"}'
    assert (tmp_path / "resource/pipeline").is_dir()
    assert (tmp_path / "resource/image").is_dir()
    cli.main(["--project", str(tmp_path), "init"])
    assert (tmp_path / ".gitignore").read_text().splitlines().count(".maa-dev/") == 1


def test_cli_error_from_service_has_nonzero_exit(running_daemon):
    project, _ = running_daemon
    result = daemon._exchange(daemon._read_state(project), "broken", {}, 5)
    assert result["ok"] is False
    assert result["error"]["code"] == "EXPECTED_ERROR"


def test_init_creates_standard_pi_v2(tmp_path):
    cli.initialize(tmp_path)
    interface = json.loads((tmp_path / "interface.json").read_text())
    assert interface["interface_version"] == 2
    assert interface["resource"] == [{"name": "Default", "path": ["./resource"]}]
    assert interface["task"] == []


def test_real_demo_cli_workflow_and_persisted_artifacts_after_restart(tmp_path):
    """Separate clients explore, probe, run and recover evidence across owner lifetimes."""
    resource = Path(__file__).parents[1] / "examples/mailbox/resource"
    assert invoke(tmp_path, "init").returncode == 0
    shutil.copytree(resource, tmp_path / "resource", dirs_exist_ok=True)

    def successful(*arguments):
        completed = invoke(tmp_path, *arguments)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        packet = json.loads(completed.stdout)
        assert packet["ok"] is True
        return packet["result"]

    try:
        session = successful("session", "open", "--backend", "demo")["session_id"]
        observation = successful("observe", "--session", session)
        mailbox = next(e for e in observation["elements"] if e["text"] == "Mailbox")
        action = successful(
            "act",
            "--session",
            session,
            "--observation",
            observation["observation_id"],
            "--target",
            mailbox["ref"],
        )
        assert action["status"] == "succeeded"
        after = action["observation"]["observation_id"]
        old = invoke(
            tmp_path,
            "act",
            "--session",
            session,
            "--observation",
            observation["observation_id"],
            "--target",
            mailbox["ref"],
        )
        assert old.returncode == 1
        assert json.loads(old.stdout)["error"]["code"] == "STALE_OBSERVATION"
        probe = successful(
            "probe", "--session", session, "--observation", after, "--node", "MailboxVisible"
        )
        assert probe["result"]["hit"] is True
        run = successful(
            "run",
            "--session",
            session,
            "--entry",
            "OpenMailbox",
            "--goal",
            "HomeVisible",
            "--timeout-seconds",
            "5",
        )
        assert run["execution_status"] == "succeeded"
        assert run["goal_status"] == "passed"
        old_pid = successful("daemon", "status")["pid"]
        successful("daemon", "stop")
        deadline = time.monotonic() + 10
        while daemon.daemon_status(tmp_path)["running"] and time.monotonic() < deadline:
            time.sleep(0.02)
        saved = successful("artifact", "--kind", "runs", "--id", run["run_id"])
        assert saved["run_id"] == run["run_id"]
        assert saved["goal_status"] == "passed"
        assert successful("daemon", "status")["pid"] != old_pid
        assert successful("session", "list")["sessions"] == []
    finally:
        daemon.stop_daemon(tmp_path)
