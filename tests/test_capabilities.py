import ast
import inspect
import json
import textwrap

import pytest
from jsonschema import Draft202012Validator

from vibe_maa import __version__, capabilities
from vibe_maa.errors import DevError
from vibe_maa.service import DevelopmentService
from vibe_maa.tasks import TaskManager


def actual_dispatch_handlers():
    """Inspect the dispatch registry without creating a service or touching a project."""
    source = ast.parse(textwrap.dedent(inspect.getsource(DevelopmentService.dispatch)))
    assignment = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "methods" for target in node.targets)
    )
    service = DevelopmentService.__new__(DevelopmentService)
    service.tasks = TaskManager.__new__(TaskManager)

    def resolve(node):
        if isinstance(node, ast.Name) and node.id == "self":
            return service
        assert isinstance(node, ast.Attribute), "Dispatch registry changed; update discovery tests"
        return getattr(resolve(node.value), node.attr)

    return {
        key.value: resolve(value)
        for key, value in zip(assignment.value.keys, assignment.value.values, strict=True)
    }


def test_discovery_covers_the_actual_dispatch_registry_without_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Capability discovery must not start or inspect a device")

    monkeypatch.setattr(DevelopmentService, "__init__", forbidden)
    monkeypatch.setattr(TaskManager, "__init__", forbidden)
    result = capabilities.show()
    assert result["schema_version"] == 1
    assert result["version"] == __version__
    assert result["readiness"] == "not_checked"
    assert {row["method"] for row in result["methods"]} == set(actual_dispatch_handlers())
    assert len(json.dumps(result).encode()) < 15_000
    assert result["backends"]["demo"]["scope"] == "fixture"
    assert "no DOM" in result["backends"]["browser"]["perception"]


def test_all_focused_schemas_match_real_parameters_defaults_and_examples():
    for method, handler in actual_dispatch_handlers().items():
        description = capabilities.show(method)
        schema = description["input_schema"]
        parameters = inspect.signature(handler).parameters
        assert set(schema["properties"]) == set(parameters), method
        assert schema["required"] == [
            name
            for name, parameter in parameters.items()
            if parameter.default is inspect.Parameter.empty
        ], method
        for name, parameter in parameters.items():
            if parameter.default is not inspect.Parameter.empty:
                assert schema["properties"][name]["default"] == parameter.default, (method, name)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(description["example"]["params"])
        with pytest.raises(TypeError):
            handler.__func__.__get__(handler.__self__)(unsupported_parameter=True)
        assert description["cli_example"].startswith("vibe-maa --project PATH ")
        assert len(json.dumps(description, allow_nan=False).encode()) < 10_000, method


@pytest.mark.parametrize(
    "params",
    [
        {"session_id": "s", "observation_id": "o"},
        {"session_id": "s", "observation_id": "o", "target": "e1", "box": [0, 0, 20, 20]},
        {"session_id": "s", "observation_id": "o", "box": [0, 0, 0, 20]},
        {"session_id": "s", "observation_id": "o", "type": "key", "box": [0, 0, 20, 20]},
        {"session_id": "s", "observation_id": "o", "type": "text", "text": "hello"},
        {"session_id": "s", "observation_id": "o", "type": "swipe", "target": "e1", "x2": 50},
        {
            "session_id": "s",
            "observation_id": "o",
            "type": "swipe",
            "target": "e1",
            "x2": 50,
            "y2": 50,
            "duration": 0,
        },
        {"session_id": "s", "observation_id": "o", "type": "shell", "target": "e1"},
        {"session_id": "s", "observation_id": "o", "target": "e1", "x": 10},
    ],
)
def test_action_schema_rejects_unguarded_or_invalid_inputs_before_a_device_call(params):
    validator = Draft202012Validator(capabilities.show("act")["input_schema"])
    assert not validator.is_valid(params)


@pytest.mark.parametrize(
    "params",
    [
        {"session_id": "s", "observation_id": "o", "target": "e1"},
        {"session_id": "s", "observation_id": "o", "box": [10, 20, 30, 40]},
        {"session_id": "s", "observation_id": "o", "type": "key", "key": 113, "target": "e1"},
        {
            "session_id": "s",
            "observation_id": "o",
            "type": "text",
            "text": "hello",
            "box": [0, 0, 100, 50],
        },
        {
            "session_id": "s",
            "observation_id": "o",
            "type": "swipe",
            "target": "e1",
            "x2": 50,
            "y2": 50,
        },
    ],
)
def test_action_schema_accepts_supported_guarded_inputs(params):
    validator = Draft202012Validator(capabilities.show("act")["input_schema"])
    validator.validate(params)


def test_session_schema_requires_explicit_backend_and_its_connection_fields():
    validator = Draft202012Validator(capabilities.show("session.open")["input_schema"])
    for invalid in [
        {},
        {"backend": "adb"},
        {"backend": "browser"},
        {"backend": "browser", "url": "file:///private/file"},
        {"backend": "win32"},
        {"backend": "demo", "width": 0},
    ]:
        assert not validator.is_valid(invalid), invalid
    for valid in [
        {"backend": "demo"},
        {"backend": "adb", "device": "127.0.0.1:16384"},
        {"backend": "browser", "url": "http://127.0.0.1:8765"},
    ]:
        validator.validate(valid)


def test_run_budget_and_task_name_schema_catch_mistakes():
    run = Draft202012Validator(capabilities.show("run")["input_schema"])
    assert not run.is_valid({"session_id": "s", "entry": "Start", "timeout_seconds": 0})
    assert not run.is_valid({"session_id": "s", "entry": "Start", "timeout_seconds": 301})
    assert not run.is_valid({"session_id": "s", "entry": "Start", "override": {"Start": 1}})
    run.validate({"session_id": "s", "entry": "Start", "override": {"Start": {"timeout": 3000}}})
    probe = Draft202012Validator(capabilities.show("probe")["input_schema"])
    probe.validate(
        {"session_id": "s", "observation_id": "o", "node": "Start", "override": {"threshold": 0.8}}
    )
    task = Draft202012Validator(capabilities.show("task.create")["input_schema"])
    example = capabilities.show("task.create")["example"]["params"]
    for patch in [
        {"name": "../outside"},
        {"objective": " "},
        {"max_attempts": 0},
        {"constraints": [""]},
        {"scenarios": {"popup": " "}},
    ]:
        assert not task.is_valid({**example, **patch}), patch


def test_semantics_and_recovery_explain_failure_without_fabricating_readiness():
    assert "offline_recognition_only" in capabilities.show()["status_fields"]["scope"]
    action = capabilities.show("act")
    assert action["recovery"]["STALE_OBSERVATION"]["next"] == "observe"
    assert "input may already" in action["recovery"]["DAEMON_UNAVAILABLE"]["instruction"]
    assert "result.failure" in action["failure_delivery"]
    run = capabilities.show("run")
    assert "goal_status" in run["returns"]
    assert any("independently" in note for note in run["notes"])
    probe = capabilities.show("probe")
    assert any("result.hit" in note for note in probe["notes"])
    assert any("Custom" in note for note in probe["notes"])
    assert "goal_status" not in probe["returns"]


def test_unknown_method_returns_actionable_stable_error():
    with pytest.raises(DevError) as caught:
        capabilities.show("click")
    assert caught.value.code == "UNKNOWN_METHOD"
    assert caught.value.details["next"] == "vibe-maa capabilities"


def test_daemon_discovery_guides_persistent_tool_sessions_without_connecting(monkeypatch):
    from vibe_maa import daemon
    from vibe_maa.cli import _parser

    def forbidden(*args, **kwargs):
        raise AssertionError("Discovery must not start or inspect the daemon")

    monkeypatch.setattr(daemon, "ensure_daemon", forbidden)
    monkeypatch.setattr(daemon, "daemon_status", forbidden)
    monkeypatch.setenv("VIBE_MAA_EXTERNAL_DAEMON_PROJECT", "PRIVATE_PROJECT_VALUE")
    monkeypatch.setenv("VIBE_MAA_EXTERNAL_DAEMON_PID", "PRIVATE_PID_VALUE")
    result = capabilities.show()
    lifetime = result["daemon"]
    assert "long-lived exec/PTY tool session" in lifetime["lifetime"]
    assert "separate call" in lifetime["lifetime"]
    assert "running and compatible" in lifetime["lifetime"]
    assert "Never loop session.open" in lifetime["owner_loss"]
    for operation in ("serve", "status", "stop"):
        arguments = _parser().parse_args(["--project", "PATH", "daemon", operation])
        assert arguments.command == "daemon"
        assert arguments.operation == operation
    assert lifetime["stop_recovery"]["next"] == "session.close"
    assert "PRIVATE_PROJECT_VALUE" not in json.dumps(result)
    assert "PRIVATE_PID_VALUE" not in json.dumps(result)


def test_daemon_failure_recovery_preserves_external_owner_and_avoids_open_loops():
    action = capabilities.show("act")
    recovery = action["recovery"]
    assert recovery["DAEMON_REQUIRES_SUPERVISOR"]["next"] == "daemon serve"
    assert "let the tool yield" in recovery["DAEMON_REQUIRES_SUPERVISOR"]["instruction"]
    assert recovery["SESSION_OWNER_LOST"]["next"] == "context"
    assert "Do not loop session.open" in recovery["SESSION_OWNER_LOST"]["instruction"]
    for code in [
        "EXTERNAL_DAEMON_CONFIGURATION",
        "EXTERNAL_DAEMON_PROJECT_MISMATCH",
        "EXTERNAL_DAEMON_UNAVAILABLE",
        "EXTERNAL_DAEMON_PID_MISMATCH",
    ]:
        assert recovery[code]["next"] == "context"
    assert (
        "do not stop or replace"
        in capabilities.show("session.open")["recovery"]["DAEMON_VERSION_MISMATCH"]["instruction"]
    )
    assert "SESSION_OWNER_LOST" not in capabilities.show("session.open")["recovery"]
    for method in ["task.create", "task.list", "task.context"]:
        assert "DAEMON_REQUIRES_SUPERVISOR" not in capabilities.show(method)["recovery"]


def test_browser_display_parameter_description_names_real_cli_flags():
    from vibe_maa.cli import _parser

    description = capabilities.show("session.open")
    display = description["input_schema"]["properties"]["headless"]
    command = ["session", "open", "--backend", "browser", "--url", "http://127.0.0.1:8765"]
    parser = _parser()
    assert parser.parse_args(command).headless == display["default"]
    for flag, expected in [("--headless", True), ("--headed", False)]:
        assert flag in display["description"]
        assert parser.parse_args([*command, flag]).headless is expected
    assert any("--topic project|" in tool for tool in capabilities.show()["cli_tools"])
