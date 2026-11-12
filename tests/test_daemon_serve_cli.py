import json

from vibe_maa import cli, daemon
from vibe_maa.errors import DevError


def test_serve_runs_foreground_without_client_dispatch(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(daemon, "run_daemon", lambda project: calls.append(project))

    def forbidden(*args, **kwargs):
        raise AssertionError("Foreground serving must not dispatch or spawn a client request")

    monkeypatch.setattr(cli, "request", forbidden)
    assert cli.main(["--project", str(tmp_path), "daemon", "serve"]) == 0
    assert calls == [tmp_path.resolve()]
    assert capsys.readouterr().out == ""


def test_serve_keeps_structured_owner_errors(tmp_path, monkeypatch, capsys):
    def owned(project):
        raise DevError("DAEMON_ALREADY_RUNNING", "A daemon already owns this project.")

    monkeypatch.setattr(daemon, "run_daemon", owned)
    assert cli.main(["--project", str(tmp_path), "daemon", "serve"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "DAEMON_ALREADY_RUNNING"


def test_serve_keyboard_interrupt_stops_without_traceback(tmp_path, monkeypatch, capsys):
    def interrupted(project):
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon, "run_daemon", interrupted)
    assert cli.main(["--project", str(tmp_path), "daemon", "serve"]) == 0
    assert capsys.readouterr().out == ""
