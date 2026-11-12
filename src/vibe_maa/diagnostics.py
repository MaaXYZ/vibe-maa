import importlib.metadata
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .errors import DevError
from .service import load_nodes


def devices(adb_path: str = "adb") -> dict[str, Any]:
    executable = shutil.which(adb_path)
    if executable is None:
        raise DevError("ADB_NOT_FOUND", "Install Android platform-tools or supply --adb-path")
    from maa.toolkit import Toolkit

    found = Toolkit.find_adb_devices(executable)
    result = []
    for device in found:
        config = device.config
        if isinstance(config, str):
            config = json.loads(config)
        result.append(
            {
                "name": device.name,
                "address": device.address,
                "adb_path": device.adb_path,
                "config": config,
            }
        )
    return {"devices": result, "adb_path": executable}


def doctor(project: Path, adb_path: str = "adb", resource_path: str = "resource") -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, status: str, detail: str, remedy: str | None = None) -> None:
        checks.append({"name": name, "status": status, "detail": detail, "remedy": remedy})

    check("python", "passed" if sys.version_info >= (3, 11) else "failed", sys.version.split()[0])
    try:
        version = importlib.metadata.version("maafw")
        from maa.define import LoggingLevelEnum
        from maa.tasker import Tasker

        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        instance = Tasker()
        del instance
        check("maafw", "passed", version)
    except (ImportError, OSError, RuntimeError) as exc:
        check("maafw", "failed", str(exc), "Reinstall vibe-maa and its native MaaFW dependency")
    bundle = (project / resource_path).resolve()
    if not bundle.is_relative_to(project.resolve()):
        raise DevError("INVALID_RESOURCE", "Resource directory must be inside the project")
    if not bundle.is_dir():
        check("resource", "failed", f"Missing {bundle}", "Run vibe-maa --project PATH init")
    else:
        try:
            nodes = load_nodes(project, resource_path)
            check(
                "pipeline",
                "passed" if nodes else "warning",
                f"{len(nodes)} node definitions",
                None if nodes else "Create a Pipeline, or run demo init in a new project",
            )
        except DevError as exc:
            check("pipeline", "failed", str(exc), "Correct the Pipeline JSON/JSONC definitions")
    required = [bundle / "model/ocr" / name for name in ["det.onnx", "rec.onnx", "keys.txt"]]
    missing = [p.name for p in required if not p.is_file()]
    check(
        "ocr",
        "warning" if missing else "passed",
        f"Missing {', '.join(missing)}" if missing else "OCR model files present",
        "Provision MaaFW-compatible det.onnx, rec.onnx, keys.txt under resource/model/ocr"
        if missing
        else None,
    )
    adb = shutil.which(adb_path)
    check(
        "adb",
        "passed" if adb else "warning",
        adb or "ADB not found",
        None if adb else "Install Android platform-tools for Android device sessions",
    )
    if importlib.util.find_spec("playwright") is None:
        check(
            "browser",
            "warning",
            "Optional browser adapter is not installed",
            "Install vibe-maa[browser], then run playwright install chromium",
        )
    else:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as runtime:
            executable = Path(runtime.chromium.executable_path)
        check(
            "browser",
            "passed" if executable.is_file() else "warning",
            str(executable) if executable.is_file() else "Chromium binary is missing",
            None if executable.is_file() else "Run playwright install chromium",
        )
    return {
        "version": __version__,
        "project": str(project.resolve()),
        "ready": not any(c["status"] == "failed" for c in checks),
        "checks": checks,
        "scope": "local_readiness_only",
        "note": "No device connection or task execution was attempted",
    }
