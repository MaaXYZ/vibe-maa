import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibe_maa.errors import DevError
from vibe_maa.evidence import write_json
from vibe_maa.tasks import TaskManager


class Service:
    def __init__(self, project: Path):
        self.project = project
        self.session = SimpleNamespace(kind="demo", device="", resource_path="resource")
        self.manager = TaskManager(project)
        self.calls = []
        self.goal_status = "passed"
        self.execution_status = "succeeded"
        self.inputs_changed = False
        self.case_passed = True
        self.failure = None
        self.persist_report = True
        self.start_hit = True
        self.start_checks = []

    def _session(self, session_id):
        assert session_id == "session-one"
        return self.session

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        result = {
            "run_id": f"run_{len(self.calls)}",
            "report_path": str(self.project / f".maa-dev/runs/run_{len(self.calls)}/record.json"),
            "session_id": kwargs["session_id"],
            "entry": kwargs["entry"],
            "goal": kwargs["goal"],
            "resource_path": "resource",
            "execution_status": self.execution_status,
            "goal_status": self.goal_status,
            "inputs": self.manager._resource_snapshot("resource"),
            "inputs_changed": self.inputs_changed,
            "backend": self.session.kind,
            "device": self.session.device,
            "scope": "fixture" if self.session.kind == "demo" else "device",
            "after_observation": "obs_after",
        }
        if self.persist_report:
            write_json(Path(result["report_path"]), result)
        return result

    def _node(self, session, name):
        return json.loads((self.project / "resource/pipeline/flow.json").read_text())[name]

    def observe(self, session_id, ocr=False):
        oid = f"obs_{len(self.start_checks)}"
        image = self.project / f".maa-dev/observations/{oid}/image.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"original start pixels")
        observation = {
            "observation_id": oid,
            "session_id": session_id,
            "image_path": str(image),
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        }
        write_json(image.parent / "record.json", observation)
        return observation

    def probe(self, session_id, observation_id, node):
        self.start_checks.append(node)
        pid = f"probe_{len(self.start_checks)}"
        probe = {
            "probe_id": pid,
            "observation_id": observation_id,
            "node": node,
            "result": {"hit": self.start_hit},
        }
        write_json(self.project / f".maa-dev/probes/{pid}/record.json", probe)
        return probe

    def case_check(self, session_id, cases):
        return {"passed": self.case_passed, "scope": "offline_recognition_only"}


@pytest.fixture
def project(tmp_path):
    pipeline = tmp_path / "resource/pipeline/flow.json"
    write_json(
        pipeline,
        {
            "Start": {"recognition": "DirectHit", "next": ["Goal"]},
            "Goal": {"recognition": "OCR", "expected": "Home"},
            "HomeStart": {"recognition": "OCR", "expected": "Home"},
            "MailboxStart": {"recognition": "OCR", "expected": "Mailbox"},
            "PopupStart": {"recognition": "OCR", "expected": "Announcement"},
        },
    )
    return tmp_path


def create(manager, **kwargs):
    return manager.create("mail", "Open the mailbox and return home", "Start", "Goal", **kwargs)


def test_task_and_attempt_survive_new_manager_instances(project):
    manager = TaskManager(project)
    context = create(manager)
    assert context["state"] == "draft"
    service = Service(project)
    result = TaskManager(project).run(service, "session-one", "mail")
    assert result["context"]["state"] == "accepted"
    restored = TaskManager(project).context("mail")
    assert restored["latest_attempt"]["run_id"] == "run_1"
    assert restored["scope"] == "fixture"
    assert restored["acceptance_scope"] == "configured_checks_only"
    assert service.calls[0] == {
        "session_id": "session-one",
        "entry": "Start",
        "goal": "Goal",
        "timeout_seconds": 20,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("goal_status", "failed"),
        ("goal_status", "unknown"),
        ("execution_status", "failed"),
        ("inputs_changed", True),
    ],
)
def test_unproven_task_never_accepted(project, field, value):
    manager = TaskManager(project)
    create(manager)
    service = Service(project)
    setattr(service, field, value)
    result = manager.run(service, "session-one", "mail")
    assert result["attempt"]["accepted"] is False
    assert result["context"]["state"] == "needs_validation"


def test_changed_source_invalidates_accepted_result(project):
    manager = TaskManager(project)
    create(manager)
    manager.run(Service(project), "session-one", "mail")
    pipeline = project / "resource/pipeline/flow.json"
    pipeline.write_text(pipeline.read_text() + "\n")
    context = TaskManager(project).context("mail")
    assert context["state"] == "needs_validation"
    assert context["freshness"] == "changed"
    assert context["latest_attempt"]["accepted"] is True


def test_changed_goal_invalidates_prior_acceptance(project):
    manager = TaskManager(project)
    create(manager)
    manager.run(Service(project), "session-one", "mail")
    path = project / "dev/tasks/mail.json"
    spec = json.loads(path.read_text())
    spec["goal"] = "WeakenedGoal"
    write_json(path, spec)
    assert manager.context("mail")["state"] == "needs_validation"


def test_selected_cases_must_pass_and_relabeling_invalidates_acceptance(project):
    manager = TaskManager(project)
    image = project / "dev/cases/home.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"sample pixels")
    cases = project / "dev/cases/check.json"
    write_json(cases, [{"node": "Goal", "image": "home.png", "expected_hit": True}])
    create(manager, cases="dev/cases/check.json")
    service = Service(project)
    service.case_passed = False
    assert manager.run(service, "session-one", "mail")["attempt"]["accepted"] is False
    service.case_passed = True
    assert manager.run(service, "session-one", "mail")["context"]["state"] == "accepted"
    write_json(cases, [{"node": "Goal", "image": "home.png", "expected_hit": False}])
    assert manager.context("mail")["state"] == "needs_validation"


def test_changed_case_image_invalidates_acceptance(project):
    manager = TaskManager(project)
    image = project / "home.png"
    image.write_bytes(b"pixels")
    write_json(
        project / "cases.json", [{"node": "Goal", "image": "home.png", "expected_hit": True}]
    )
    create(manager, cases="cases.json")
    manager.run(Service(project), "session-one", "mail")
    image.write_bytes(b"different pixels")
    assert manager.context("mail")["state"] == "needs_validation"


def test_error_attempts_are_durable_and_consume_budget(project):
    manager = TaskManager(project)
    create(manager, max_attempts=1)
    service = Service(project)
    service.failure = DevError("DEVICE_TIMEOUT", "Disconnected")
    result = manager.run(service, "session-one", "mail")
    assert result["attempt"]["status"] == "error"
    assert result["attempt"]["error"]["code"] == "DEVICE_TIMEOUT"
    assert TaskManager(project).context("mail")["state"] == "blocked"
    with pytest.raises(DevError) as caught:
        manager.run(service, "session-one", "mail")
    assert caught.value.code == "TASK_BUDGET_EXHAUSTED"
    assert len(service.calls) == 1


def test_pending_attempt_counts_against_budget(project):
    manager = TaskManager(project)
    context = create(manager, max_attempts=1)
    path = project / ".maa-dev/tasks/mail/attempts/attempt_pending.json"
    write_json(
        path,
        {
            "attempt_id": "attempt_pending",
            "status": "pending",
            "started_at": "2026-01-01",
            "spec_sha256": context["spec_sha256"],
            "resource_path": "resource",
            "accepted": False,
        },
    )
    with pytest.raises(DevError, match="budget exhausted"):
        TaskManager(project).run(Service(project), "session-one", "mail")
    assert manager.context("mail")["attempt_count"] == 1


def test_constraints_are_kept_without_claiming_automatic_enforcement(project):
    manager = TaskManager(project)
    create(manager, constraints=["Do not spend premium currency"])
    result = manager.run(Service(project), "session-one", "mail")
    assert result["context"]["constraints"] == ["Do not spend premium currency"]
    assert result["context"]["constraints_status"] == "not_automatically_verified"


@pytest.mark.parametrize("name", ["../escape", "", "a/b", ".hidden", "a b"])
def test_task_names_cannot_escape_project(project, name):
    with pytest.raises(DevError):
        TaskManager(project).create(name, "objective", "Start", "Goal")


def test_task_create_never_overwrites(project):
    manager = TaskManager(project)
    create(manager)
    before = (project / "dev/tasks/mail.json").read_bytes()
    with pytest.raises(DevError) as caught:
        create(manager)
    assert caught.value.code == "TASK_EXISTS"
    assert (project / "dev/tasks/mail.json").read_bytes() == before


def test_case_files_cannot_escape_project(project):
    with pytest.raises(DevError):
        create(TaskManager(project), cases="../cases.json")


def test_invalid_case_attempt_retains_pre_mutation_error(project):
    manager = TaskManager(project)
    create(manager, cases="missing.json")
    service = Service(project)
    result = manager.run(service, "session-one", "mail")
    assert result["attempt"]["error"]["code"] == "INVALID_CASES"
    assert service.calls == []


def test_list_exposes_tasks_without_losing_invalid_spec(project):
    manager = TaskManager(project)
    create(manager)
    (project / "dev/tasks/broken.json").write_text("not-json")
    tasks = manager.list()["tasks"]
    assert {item["name"]: item["state"] for item in tasks} == {"broken": "invalid", "mail": "draft"}


@pytest.mark.parametrize("change", ["delete", "edit", "wrong_run"])
def test_report_proof_must_remain_present_and_unchanged(project, change):
    manager = TaskManager(project)
    create(manager)
    accepted = manager.run(Service(project), "session-one", "mail")
    path = Path(accepted["attempt"]["report_path"])
    if change == "delete":
        path.unlink()
    elif change == "edit":
        path.write_text(path.read_text() + "\n")
    else:
        report = json.loads(path.read_text())
        report["run_id"] = "run_other"
        write_json(path, report)
        # Even a matching digest cannot turn a different run into this proof.
        attempt_path = Path(accepted["attempt"]["attempt_path"])
        attempt = json.loads(attempt_path.read_text())
        attempt["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(attempt_path, attempt)
    restored = TaskManager(project).context("mail")
    assert restored["state"] == "needs_validation"
    assert restored["proofs"] == {}


def test_success_without_a_saved_run_report_is_unaccepted(project):
    manager = TaskManager(project)
    create(manager)
    service = Service(project)
    service.persist_report = False
    result = manager.run(service, "session-one", "mail")
    assert result["attempt"]["accepted"] is False
    assert result["context"]["state"] == "needs_validation"
    assert result["attempt"]["report_error"]


def test_all_required_verified_scenarios_are_needed(project):
    manager = TaskManager(project)
    scenarios = {"home": "HomeStart", "mailbox": "MailboxStart", "popup": "PopupStart"}
    create(manager, scenarios=scenarios)
    service = Service(project)
    with pytest.raises(DevError) as caught:
        manager.run(service, "session-one", "mail")
    assert caught.value.code == "SCENARIO_REQUIRED"
    one = manager.run(service, "session-one", "mail", scenario="home")
    assert one["context"]["state"] == "needs_validation"
    assert one["context"]["completed_scenarios"] == ["home"]
    assert one["context"]["missing_scenarios"] == ["mailbox", "popup"]
    manager.run(service, "session-one", "mail", scenario="mailbox")
    final = manager.run(service, "session-one", "mail", scenario="popup")
    assert final["context"]["state"] == "accepted"
    assert final["context"]["required_scenarios"] == list(scenarios)
    assert final["context"]["missing_scenarios"] == []
    assert set(final["context"]["proofs"]) == set(scenarios)
    assert service.start_checks == list(scenarios.values())


def test_start_check_failure_consumes_attempt_without_running(project):
    manager = TaskManager(project)
    create(manager, scenarios={"home": "HomeStart"})
    service = Service(project)
    service.start_hit = False
    result = manager.run(service, "session-one", "mail", scenario="home")
    assert result["attempt"]["error"]["code"] == "START_STATE_MISMATCH"
    assert service.calls == []
    assert result["context"]["attempt_count"] == 1
    assert result["context"]["missing_scenarios"] == ["home"]


def test_unconditional_start_is_rejected(project):
    manager = TaskManager(project)
    create(manager, scenarios={"home": "Start"})
    service = Service(project)
    result = manager.run(service, "session-one", "mail", scenario="home")
    assert result["attempt"]["error"]["code"] == "INVALID_START_NODE"
    assert service.calls == []
    assert service.start_checks == []


def test_newer_failure_invalidates_that_scenarios_older_pass(project):
    manager = TaskManager(project)
    create(manager, scenarios={"home": "HomeStart", "popup": "PopupStart"})
    service = Service(project)
    manager.run(service, "session-one", "mail", scenario="home")
    assert (
        manager.run(service, "session-one", "mail", scenario="popup")["context"]["state"]
        == "accepted"
    )
    service.goal_status = "failed"
    failed = manager.run(service, "session-one", "mail", scenario="home")
    assert failed["context"]["state"] == "needs_validation"
    assert failed["context"]["completed_scenarios"] == ["popup"]
    assert failed["context"]["latest_attempt"]["goal_status"] == "failed"


def test_start_proof_deletion_invalidates_scenario(project):
    manager = TaskManager(project)
    create(manager, scenarios={"home": "HomeStart"})
    accepted = manager.run(Service(project), "session-one", "mail")
    Path(accepted["attempt"]["start_check"]["image_path"]).unlink()
    assert manager.context("mail")["state"] == "needs_validation"


def test_scenario_proofs_cannot_mix_fixture_and_device(project):
    manager = TaskManager(project)
    create(manager, scenarios={"home": "HomeStart", "popup": "PopupStart"})
    service = Service(project)
    manager.run(service, "session-one", "mail", scenario="home")
    service.session.kind = "adb"
    service.session.device = "127.0.0.1:16384"
    result = manager.run(service, "session-one", "mail", scenario="popup")
    assert result["context"]["state"] == "needs_validation"
    assert result["context"]["scope"] == "device"
    assert result["context"]["completed_scenarios"] == ["popup"]
    assert result["context"]["missing_scenarios"] == ["home"]


@pytest.mark.parametrize("scenarios", [{"bad label": "HomeStart"}, {"home": ""}, ["home"]])
def test_invalid_scenarios_are_rejected(project, scenarios):
    with pytest.raises(DevError):
        create(TaskManager(project), scenarios=scenarios)


def test_real_starter_cannot_accept_popup_objective_after_home_only(tmp_path):
    from vibe_maa.bootstrap import initialize_demo
    from vibe_maa.service import DevelopmentService

    initialize_demo(tmp_path, starter=True)
    path = tmp_path / "resource/pipeline/mailbox.json"
    pipeline = json.loads(path.read_text())
    pipeline["PopupVisible"] = {
        "recognition": "ColorMatch",
        "roi": [0, 0, 10, 10],
        "lower": [60, 60, 60],
        "upper": [60, 60, 60],
        "count": 50,
        "action": "DoNothing",
    }
    write_json(path, pipeline)
    manager = TaskManager(tmp_path)
    manager.create(
        "mail",
        "Open mailbox from all three starting states and return home",
        "OpenMailbox",
        "HomeVisible",
        cases="dev/cases/mailbox.json",
        scenarios={"home": "HomeVisible", "mailbox": "MailboxVisible", "popup": "PopupVisible"},
    )
    service = DevelopmentService(tmp_path)
    try:
        home = service.session_open(backend="demo", initial_state="home")["session_id"]
        first = manager.run(service, home, "mail", scenario="home", timeout_seconds=3)
        assert first["attempt"]["accepted"] is True
        assert first["context"]["state"] == "needs_validation"
        assert first["context"]["missing_scenarios"] == ["mailbox", "popup"]
        popup = service.session_open(backend="demo", initial_state="popup")["session_id"]
        failed = manager.run(service, popup, "mail", scenario="popup", timeout_seconds=3)
        assert failed["attempt"]["start_check"]["hit"] is True
        assert failed["attempt"]["execution_status"] == "failed"
        assert failed["context"]["state"] == "needs_validation"
        assert "popup" in failed["context"]["missing_scenarios"]
    finally:
        service.close()
