import importlib.util
import os
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).parent


def _module(name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_key_redaction(monkeypatch):

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    assert module.redact("prefix secret suffix", "secret") == "prefix [REDACTED] suffix"


def test_configuration_requires_real_key_and_model(monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    monkeypatch.delenv("VIBE_MAA_LLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Secret"):
        module.configuration(False)
    monkeypatch.setenv("VIBE_MAA_LLM_API_KEY", "do-not-record")
    monkeypatch.delenv("VIBE_MAA_LLM_MODEL", raising=False)
    with pytest.raises(ValueError, match="Variable"):
        module.configuration(False)
    monkeypatch.setenv("VIBE_MAA_LLM_MODEL", "provider-supplied-model")
    configured = module.configuration(False)
    assert configured["wire_api"] == "responses"
    assert "do-not-record" not in str(configured)


def test_run_reasoning_override_preserves_repository_model_and_setting(monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    monkeypatch.setenv("VIBE_MAA_LLM_API_KEY", "fixture-key-not-for-logs")
    monkeypatch.setenv("VIBE_MAA_LLM_MODEL", "repository-model")
    monkeypatch.setenv("VIBE_MAA_LLM_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("VIBE_MAA_LLM_REASONING_EFFORT", "high")
    configured = module.configuration(False)
    low = module.configuration(False, "low")
    assert configured["reasoning_effort_requested"] == "high"
    assert configured["configuration_source"]["reasoning_effort"] == "environment"
    assert low["reasoning_effort_requested"] == "low"
    assert low["configuration_source"]["reasoning_effort"] == "run_override"
    assert low["model"] == configured["model"] == "repository-model"
    assert low["base_url"] == "https://example.test/v1"
    assert os.environ["VIBE_MAA_LLM_REASONING_EFFORT"] == "high"
    assert "fixture-key-not-for-logs" not in str(low)


def test_unset_reasoning_and_local_configuration_remain_explicitly_unknown(monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    monkeypatch.setenv("VIBE_MAA_LLM_API_KEY", "fixture-key")
    monkeypatch.setenv("VIBE_MAA_LLM_MODEL", "repository-model")
    monkeypatch.delenv("VIBE_MAA_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("VIBE_MAA_LLM_REASONING_EFFORT", raising=False)
    configured = module.configuration(False)
    assert configured["reasoning_effort_requested"] == "provider_default"
    assert configured["configuration_source"]["reasoning_effort"] == "provider_default"
    assert module.configuration(True)["reasoning_effort_requested"] == "local_harness_default"
    assert module.configuration(True, "low")["reasoning_effort_requested"] == "low"


@pytest.mark.parametrize(
    "url",
    [
        "https://fixture-secret@example.test/v1",
        "https://user:fixture-secret@example.test/v1",
        "https://example.test/v1?api_key=fixture-secret",
        "https://example.test/v1#fixture-secret",
        "https:///v1",
        "http://example.test/v1",
    ],
)
def test_api_base_cannot_publish_embedded_credentials(monkeypatch, url):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    monkeypatch.setenv("VIBE_MAA_LLM_API_KEY", "fixture-key")
    monkeypatch.setenv("VIBE_MAA_LLM_MODEL", "repository-model")
    monkeypatch.setenv("VIBE_MAA_LLM_BASE_URL", url)
    with pytest.raises(ValueError) as error:
        module.configuration(False)
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize("effort", ["low", "configured"])
def test_execute_passes_only_the_requested_reasoning_to_harness(tmp_path, monkeypatch, effort):
    import subprocess
    import sys
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    monkeypatch.setenv("VIBE_MAA_LLM_API_KEY", "fixture-key-not-for-logs")
    monkeypatch.setenv("VIBE_MAA_LLM_MODEL", "repository-model")
    monkeypatch.delenv("VIBE_MAA_LLM_REASONING_EFFORT", raising=False)
    monkeypatch.setenv("GH_TOKEN", "unrelated-github-token")
    monkeypatch.setenv("GITHUB_TOKEN", "unrelated-github-token")
    captured = []
    real_popen = subprocess.Popen
    records = [
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": "item_1",
                "command": "failing trial",
                "status": "completed",
                "exit_code": 2,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": "item_2",
                "command": "successful trial",
                "status": "completed",
                "exit_code": 0,
            },
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
    ]
    child = (
        "import json; records="
        + repr(records)
        + "; [print(json.dumps(r),flush=True) for r in records]"
    )

    def start(command, **kwargs):
        captured.append((command, kwargs["env"]))
        return real_popen([sys.executable, "-c", child], **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", start)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="codex-cli fixture")
    )
    private = tmp_path / "private"
    private.mkdir()
    result = module.execute(
        "fixture",
        tmp_path,
        "fixture prompt",
        module.configuration(False, effort),
        3,
        private,
        False,
    )
    command, environment = captured[0]
    setting = [value for value in command if value.startswith("model_reasoning_effort=")]
    assert setting == (['model_reasoning_effort="low"'] if effort == "low" else [])
    assert command[command.index("--model") + 1] == "repository-model"
    assert "fixture-key-not-for-logs" not in " ".join(command)
    assert "GH_TOKEN" not in environment and "GITHUB_TOKEN" not in environment
    assert result["tool_items"] == 2
    assert result["failed_command_items"] == 1
    assert result["elapsed_seconds"] >= 0
    assert 'default_permissions="vibe_agent"' in command
    assert "--sandbox" not in command
    assert any(value.startswith("permissions.vibe_agent.filesystem=") for value in command)


def test_grader_rejects_executable_pipeline(tmp_path):
    import json

    grader = _module("grader")
    path = tmp_path / "resource/pipeline"
    path.mkdir(parents=True)
    (path / "agent.json").write_text(
        json.dumps({"AgentTask": {"action": "Command", "exec": "echo"}})
    )
    with pytest.raises(Exception, match="Disallowed"):
        grader.validate_submission(tmp_path)


def test_publication_rejects_secret_inside_zip(tmp_path, monkeypatch):
    import zipfile

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    archive = tmp_path / "automation.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("resource/pipeline/leak.json", "do-not-upload-this-key")
    with pytest.raises(ValueError, match="Publication rejected"):
        module.verify_publication(tmp_path, "do-not-upload-this-key")
    assert not archive.exists()


def test_drawio_default_label_renderer_is_a_rectangle():
    grader = _module("grader")
    rectangle = {
        "label": "Vibe Maa",
        "shape": "label",
        "rectangle_renderer": True,
        "ellipse_renderer": False,
        "width": 120,
        "height": 60,
    }
    assert grader.completed_drawio_document([], [rectangle]) is True
    ellipse = {
        **rectangle,
        "shape": "ellipse",
        "rectangle_renderer": False,
        "ellipse_renderer": True,
    }
    assert grader.completed_drawio_document([], [ellipse]) is False
    assert grader.completed_drawio_document([rectangle], [rectangle]) is False


def test_activity_publishes_only_safe_tool_fields(monkeypatch):
    import json

    monkeypatch.syspath_prepend(str(DIRECTORY))
    activity = _module("activity")
    trace = activity.ActivityTrace("real-fixture-api-key")
    trace.add(
        {
            "type": "item.completed",
            "item": {
                "type": "reasoning",
                "text": "private chain of thought",
                "encrypted_content": "encrypted-private-value",
            },
        }
    )
    trace.add(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "unpublished model text"},
        }
    )
    trace.add(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": "item_4",
                "status": "completed",
                "command": "printf real-fixture-api-key",
                "exit_code": 0,
                "session_token": "private-session-token",
                "encrypted_content": "encrypted-private-value",
                "aggregated_output": json.dumps(
                    {
                        "result": "done",
                        "authorization": "Bearer private-bearer",
                        "session_token": "private-session-token",
                        "encrypted_content": "encrypted-private-value",
                        "reasoning_content": "private chain of thought",
                    }
                ),
            },
        }
    )
    saved = json.dumps(trace.result())
    for private in [
        "real-fixture-api-key",
        "private-session-token",
        "encrypted-private-value",
        "private-bearer",
        "private chain of thought",
        "unpublished model text",
    ]:
        assert private not in saved
    assert '"tool_type": "command_execution"' in saved
    assert "done" in saved
    assert trace.progress(31.6) == {
        "event": "agent.progress",
        "elapsed_seconds": 31,
        "parsed_events": 3,
        "tool_items": 1,
    }


def test_activity_tracks_recovered_errors_and_usage(monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    activity = _module("activity")
    trace = activity.ActivityTrace()
    trace.add({"type": "turn.failed", "error": {"message": "temporary retry error"}})
    trace.add({"type": "turn.started"})
    trace.add({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 6}})
    trace.add({"type": "error", "message": "recovered transport warning"})
    trace.add(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 30,
                "output_tokens": 8,
                "cached_input_tokens": 25,
                "encrypted_content": "private",
            },
        }
    )
    result = trace.result()
    assert result["terminal_state"] == "completed"
    assert trace.terminal_error is None
    assert len(result["historical_errors"]) == 2
    assert result["usage"] == {"input_tokens": 50, "output_tokens": 14, "cached_input_tokens": 25}
    assert result["completed_turns"] == 2
    assert "encrypted_content" not in result["usage"]


def test_activity_budget_and_partial_reads(monkeypatch):
    import io
    import json

    monkeypatch.syspath_prepend(str(DIRECTORY))
    activity = _module("activity")
    trace = activity.ActivityTrace(max_bytes=32768)
    event = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "id": "item_unicode",
            "command": "界面",
            "status": "completed",
            "aggregated_output": "x" * 9000,
        },
    }
    data = json.dumps(event, ensure_ascii=False).encode() + b"\n"
    trace.read(io.BytesIO(data[:33]))
    assert trace.progress(0)["parsed_events"] == 0
    trace.read(io.BytesIO(data[33:]))
    for _ in range(20):
        trace.add(event)
    result = trace.result()
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 32768
    assert result["truncated_records"] > 0


def test_execute_preserves_success_after_recovered_error(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    real_popen = subprocess.Popen
    records = [
        {"type": "error", "message": "transient issue"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": "item_1",
                "command": "echo ok",
                "status": "completed",
                "aggregated_output": "ok",
                "exit_code": 0,
            },
        },
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
    ]
    child = (
        "import json; records="
        + repr(records)
        + "; [print(json.dumps(r),flush=True) for r in records]"
    )

    def start(_command, **kwargs):
        return real_popen([sys.executable, "-c", child], **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", start)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="codex-cli fixture")
    )
    private = tmp_path / "private"
    private.mkdir()
    result = module.execute("fixture", tmp_path, "fixture prompt", {}, 3, private, True)
    assert result["returncode"] == 0
    assert result["terminal_state"] == "completed"
    assert result["error"] is None
    assert result["historical_error_count"] == 1
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 4}
    trace = json.loads((tmp_path / "published/agent-activity.json").read_text())
    assert len(trace["records"]) == 1


def test_benchmark_defaults_cannot_hide_executable_actions(tmp_path):
    import json

    grader = _module("grader")
    resource = tmp_path / "resource"
    (resource / "pipeline").mkdir(parents=True)
    (resource / "pipeline/task.json").write_text(json.dumps({"AgentTask": {"action": "DoNothing"}}))
    (resource / "default_pipeline.json").write_text(json.dumps({"Default": {"action": "Command"}}))
    with pytest.raises(Exception, match="default_pipeline"):
        grader.validate_submission(tmp_path)


def test_replay_verifies_original_manifest(tmp_path, monkeypatch):
    import hashlib
    import json

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("regrade")
    source = tmp_path / "source.json"
    source.write_bytes(b"original")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "path": "source.json",
                        "sha256": hashlib.sha256(b"original").hexdigest(),
                        "size": 8,
                    }
                ]
            }
        )
    )
    assert module.verify_manifest(tmp_path.resolve())["verified_files"] == 1
    source.write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum mismatch"):
        module.verify_manifest(tmp_path.resolve())


def _trace_overflow_records():
    records = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": f"item_{index}",
                "command": "trial",
                "exit_code": 1 if index == 0 else 0,
                "aggregated_output": "x" * 6000,
            },
        }
        for index in range(8)
    ]
    records.extend(
        {"type": "error", "error": {"message": "m" * 2000, "code": "c" * 2000, "type": "t" * 2000}}
        for _ in range(4)
    )
    records.append({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return records


def test_final_trace_metrics_follow_post_serialization_trimming(monkeypatch):
    import json

    monkeypatch.syspath_prepend(str(DIRECTORY))
    activity = _module("activity")
    trace = activity.ActivityTrace(max_bytes=65536)
    for record in _trace_overflow_records():
        trace.add(record)
    assert len(trace.events) == 8 and trace.dropped == 0
    assert activity.retained_command_metrics(trace.events)["failed_command_items"] == 1
    result = trace.result()
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 65536
    assert len(result["records"]) < len(trace.events)
    assert result["truncated_records"] == len(trace.events) - len(result["records"])
    assert (
        result["failed_command_items"] == 0
    )  # The failed command was trimmed from the publication.
    assert result["completed_command_items"] == len(result["records"])
    assert trace.result() == result  # Finalization must not mutate accumulated evidence.


def test_execute_metrics_match_the_exact_published_trace_after_trimming(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    real_popen, real_trace = subprocess.Popen, module.ActivityTrace
    records_path = tmp_path / "events.json"
    records_path.write_text(json.dumps(_trace_overflow_records()), encoding="utf-8")
    child = "import json,sys; records=json.load(open(sys.argv[1])); [print(json.dumps(r),flush=True) for r in records]"

    def start(_command, **kwargs):
        return real_popen([sys.executable, "-c", child, str(records_path)], **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", start)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="codex-cli fixture")
    )
    monkeypatch.setattr(module, "ActivityTrace", lambda secret: real_trace(secret, max_bytes=65536))
    private = tmp_path / "private"
    private.mkdir()
    result = module.execute("fixture", tmp_path, "fixture prompt", {}, 3, private, True)
    published = json.loads((tmp_path / "published/agent-activity.json").read_text(encoding="utf-8"))
    assert published["truncated_records"] > 0
    assert result["failed_command_items"] == published["failed_command_items"] == 0
    assert result["activity_truncated_records"] == published["truncated_records"]
    assert result["tool_items"] == published["tool_items"] == 8
    assert result["usage"] == published["usage"]
    assert result["terminal_state"] == published["terminal_state"] == "completed"


def test_mixed_native_logs_and_json_never_publish_daemon_auth(monkeypatch):
    import json

    monkeypatch.syspath_prepend(str(DIRECTORY))
    activity = _module("activity")
    text = (
        "native log: service started\n"
        + json.dumps(
            {
                "pid": 123,
                "token": "fixture-daemon-credential",
                "port": 456,
                "auth": {"session_token": "fixture-session-credential"},
            },
            indent=2,
        )
        + "\nadditional native log\n"
        + '{"type":"reasoning","text":"private-model-thought","encrypted_content":"private-encrypted-payload"}\n'
        + "token = 'fixture-plain-token'\nAuthorization: Bearer fixture-header-token"
    )
    sanitized = activity.safe_text(text, None)
    for value in [
        "fixture-daemon-credential",
        "fixture-session-credential",
        "fixture-plain-token",
        "fixture-header-token",
        "private-model-thought",
        "private-encrypted-payload",
    ]:
        assert value not in sanitized
    assert "native log: service started" in sanitized
    assert '"pid": 123' in sanitized and '"port": 456' in sanitized


def test_harness_starts_and_checks_service_without_preopening_sessions(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    started = []
    status = {
        "running": True,
        "compatible": True,
        "stopping": False,
        "project": str(tmp_path.resolve()),
        "pid": 123,
        "version": "fixture",
        "build_id": "fixture-build",
        "token": "must-not-copy-this",
    }
    monkeypatch.setattr(module, "ensure_daemon", lambda project: started.append(project))
    monkeypatch.setattr(module, "daemon_status", lambda project: status)
    lease = module.start_tool_service(tmp_path)
    assert started == [tmp_path]
    assert lease["ownership"] == "harness" and lease["pid"] == 123
    assert "token" not in lease and "must-not-copy-this" not in str(lease)
    status["pid"] = 321
    assert module.tool_service_error(tmp_path, lease)["code"] == "HARNESS_DAEMON_REPLACED"
    status["running"] = False
    assert module.tool_service_error(tmp_path, lease)["code"] == "HARNESS_DAEMON_LOST"


def test_prompt_explicitly_states_service_budget_and_viewport(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    prompt = module.prompt_for("drawio", tmp_path, "http://127.0.0.1:1234", 600)
    assert "600 seconds" in prompt and "supervisor deadline" in prompt
    assert "--width 1280 --height 720" in prompt
    assert "Do not stop, restart, or launch a daemon" in prompt
    assert "open your own sessions" in prompt


def test_execute_inherits_external_service_and_does_not_recheck_completed_agent(
    tmp_path, monkeypatch
):
    import json
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    calls, captured = [], []
    lease = {
        "project": str(tmp_path.resolve()),
        "pid": 123,
        "build_id": "build",
        "version": "fixture",
    }

    def healthy(project, expected):
        calls.append((project, expected))
        return None

    class Completed:
        def __init__(self, _command, **kwargs):
            captured.append(kwargs["env"])
            kwargs["stdout"].write(
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}) + "\n"
            )
            kwargs["stdout"].flush()

        def poll(self):
            return 0

    monkeypatch.setattr(module, "tool_service_error", healthy)
    monkeypatch.setattr(module.subprocess, "Popen", Completed)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="fixture"))
    private = tmp_path / "private"
    private.mkdir()
    result = module.execute(
        "fixture", tmp_path, "prompt", {}, 3, private, True, external_service=lease
    )
    assert captured[0]["VIBE_MAA_EXTERNAL_DAEMON_PROJECT"] == str(tmp_path.resolve())
    assert captured[0]["VIBE_MAA_EXTERNAL_DAEMON_PID"] == "123"
    assert len(calls) == 1  # Startup only: no health error may overwrite an already-completed turn.
    assert result["error"] is None and result["tool_service"] == lease


def test_execute_aborts_on_lost_service_as_infrastructure_failure(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("run")
    lease = {
        "project": str(tmp_path.resolve()),
        "pid": 123,
        "build_id": "build",
        "version": "fixture",
    }
    checks = []

    def health(project, expected):
        checks.append(project)
        return (
            None
            if len(checks) == 1
            else {"code": "HARNESS_DAEMON_LOST", "message": "fixture service died"}
        )

    class Running:
        def __init__(self, _command, **kwargs):
            kwargs["stdout"].write(json.dumps({"type": "turn.started"}) + "\n")
            kwargs["stdout"].flush()

        def poll(self):
            return None

    clock = iter([0, 6, 6, 7, 7])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(module, "tool_service_error", health)
    monkeypatch.setattr(module, "_terminate", lambda process: -15)
    monkeypatch.setattr(module.subprocess, "Popen", Running)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="fixture"))
    private = tmp_path / "private"
    private.mkdir()
    result = module.execute(
        "fixture", tmp_path, "prompt", {}, 30, private, True, external_service=lease
    )
    assert result["returncode"] == -15 and result["timed_out"] is False
    assert result["infrastructure_error"]["code"] == "HARNESS_DAEMON_LOST"
    assert result["error"] == result["infrastructure_error"]


def test_checkout_projection_blocks_answers_but_preserves_installed_tool(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("isolation")
    root = tmp_path / "checkout"
    for relative in [
        "src",
        ".venv",
        "integration/agent/node_modules",
        "examples/answer",
        "docs/records",
        "tests",
        ".git",
    ]:
        (root / relative).mkdir(parents=True)
    (root / "integration/agent/grader.py").write_text("protected grader")
    (root / "README.md").write_text("protected checkout documentation")
    project = root / "work/run/project"
    project.mkdir(parents=True)
    (project.parent / "private").mkdir()
    rules = module.filesystem_policy(project, root)
    assert rules[str(root / "src")] == rules[str(root / ".venv")] == "read"
    assert rules[str(root / "integration/agent/node_modules")] == "read"
    assert rules[str(project)] == "write"
    for path in [
        root / "examples",
        root / "docs",
        root / "tests",
        root / ".git",
        root / "integration/agent/grader.py",
        project.parent / "private",
    ]:
        assert rules[str(path)] == "deny"
    assert str(root) not in rules  # No denied parent that could hide a required runtime descendant.


def test_private_auth_and_logs_are_denied_for_project_outside_checkout(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(DIRECTORY))
    module = _module("isolation")
    root = tmp_path / "checkout"
    root.mkdir()
    project = tmp_path / "external-run/project"
    project.mkdir(parents=True)
    private, published = project.parent / "private", project.parent / "published"
    private.mkdir()
    published.mkdir()
    rules = module.filesystem_policy(project, root)
    assert rules[str(project)] == "write"
    assert rules[str(private)] == rules[str(published)] == "deny"
