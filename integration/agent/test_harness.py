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
