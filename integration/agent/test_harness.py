import importlib.util
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
