import json
from types import SimpleNamespace

import pytest

from vibe_maa import diagnostics
from vibe_maa.errors import DevError
from vibe_maa.evidence import write_json


@pytest.fixture(autouse=True)
def no_optional_browser(monkeypatch):
    monkeypatch.setattr(diagnostics.importlib.util, "find_spec", lambda name: None)


def checks(result):
    return {item["name"]: item for item in result["checks"]}


def test_ready_resource_reports_optional_capability_gaps_without_connecting(tmp_path, monkeypatch):
    write_json(tmp_path / "resource/pipeline/start.json", {"Start": {"recognition": "DirectHit"}})
    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: None)
    result = diagnostics.doctor(tmp_path)
    found = checks(result)
    assert result["ready"] is True
    assert result["scope"] == "local_readiness_only"
    assert found["maafw"]["status"] == "passed"
    assert found["pipeline"]["status"] == "passed"
    assert found["ocr"]["status"] == "warning"
    assert "det.onnx" in found["ocr"]["detail"]
    assert found["adb"]["status"] == "warning"
    assert found["browser"]["status"] == "warning"
    assert all(found[name]["remedy"] for name in ["ocr", "adb", "browser"])
    assert "No device connection" in result["note"]
    assert not (tmp_path / ".maa-dev").exists()


def test_missing_resource_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: None)
    result = diagnostics.doctor(tmp_path)
    assert result["ready"] is False
    assert checks(result)["resource"]["status"] == "failed"
    assert "init" in checks(result)["resource"]["remedy"]


def test_invalid_pipeline_is_actionable(tmp_path, monkeypatch):
    path = tmp_path / "resource/pipeline/broken.json"
    path.parent.mkdir(parents=True)
    path.write_text("invalid JSON")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: None)
    result = diagnostics.doctor(tmp_path)
    assert result["ready"] is False
    assert checks(result)["pipeline"]["status"] == "failed"
    assert "JSON/JSONC" in checks(result)["pipeline"]["remedy"]


def test_present_models_and_explicit_adb_path_are_reported(tmp_path, monkeypatch):
    bundle = tmp_path / "resources/base"
    write_json(bundle / "pipeline/start.json", {"Start": {"recognition": "DirectHit"}})
    model = bundle / "model/ocr"
    model.mkdir(parents=True)
    for name in ["det.onnx", "rec.onnx", "keys.txt"]:
        (model / name).write_bytes(b"test file, not a validated model")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: "/tools/custom-adb")
    result = diagnostics.doctor(tmp_path, adb_path="custom-adb", resource_path="resources/base")
    found = checks(result)
    assert found["ocr"]["status"] == "passed"
    assert found["ocr"]["detail"] == "OCR model files present"
    assert found["adb"]["status"] == "passed"
    assert found["adb"]["detail"] == "/tools/custom-adb"


def test_doctor_rejects_resource_path_escape(tmp_path):
    with pytest.raises(DevError) as caught:
        diagnostics.doctor(tmp_path, resource_path="../outside")
    assert caught.value.code == "INVALID_RESOURCE"


def test_native_initialization_failure_is_a_failed_check(tmp_path, monkeypatch):
    from maa import tasker

    class UnavailableTasker:
        @staticmethod
        def set_stdout_level(level):
            pass

        def __init__(self):
            raise RuntimeError("Native runtime unavailable")

    monkeypatch.setattr(tasker, "Tasker", UnavailableTasker)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: None)
    result = diagnostics.doctor(tmp_path)
    assert result["ready"] is False
    assert checks(result)["maafw"]["status"] == "failed"
    assert "Native runtime unavailable" in checks(result)["maafw"]["detail"]


def test_devices_missing_adb_has_a_stable_error(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: None)
    with pytest.raises(DevError) as caught:
        diagnostics.devices("not-installed")
    assert caught.value.code == "ADB_NOT_FOUND"


def test_devices_normalizes_native_config_without_connecting(monkeypatch):
    from maa.toolkit import Toolkit

    seen = []

    def discover(executable):
        seen.append(executable)
        return [
            SimpleNamespace(
                name="emulator",
                address="127.0.0.1:16384",
                adb_path="/tools/adb",
                config=json.dumps({"client_type": "emulator"}),
            ),
            SimpleNamespace(name="phone", address="serial", adb_path="/tools/adb", config={}),
        ]

    monkeypatch.setattr(diagnostics.shutil, "which", lambda executable: "/tools/adb")
    monkeypatch.setattr(Toolkit, "find_adb_devices", discover)
    result = diagnostics.devices()
    assert seen == ["/tools/adb"]
    assert result["devices"][0]["config"] == {"client_type": "emulator"}
    assert result["devices"][1]["config"] == {}
    assert result["adb_path"] == "/tools/adb"
