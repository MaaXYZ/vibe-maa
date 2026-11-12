"""Verify declared draw.io task starts and recover acceptance without calling a model."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from grader import drawio_server, drawio_start_url, write_result
from regrade import source_hashes

import vibe_maa
from vibe_maa.reports import Reports
from vibe_maa.service import DevelopmentService
from vibe_maa.tasks import TaskManager


def validate(
    project: Path, output: Path, cache: Path, browser_executable: str | None = None
) -> dict:
    project, output = project.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a new output path for every task validation")
    output.mkdir(parents=True)
    before = source_hashes(project)
    manager = TaskManager(project)
    attempts = []
    spec = manager.context("AgentCanvas")
    urls = spec.get("scenario_urls") or {}
    if set(urls) != {"blank_start", "splash_start"}:
        raise ValueError("Declare blank_start and splash_start URLs before task validation")
    address = urlsplit(urls["blank_start"])
    if address.hostname != "127.0.0.1" or address.port is None or address.port < 1024:
        raise ValueError("The local acceptance server requires a declared loopback port >=1024")
    with drawio_server(cache, port=address.port) as (url, upstream):
        expected_urls = {
            name + "_start": drawio_start_url(url, name) for name in ("blank", "splash")
        }
        if spec.get("scenario_urls") != expected_urls:
            raise ValueError("Declare the exact pinned blank/splash launch URLs before validation")
        for state in ("blank", "splash"):
            service = DevelopmentService(project)
            try:
                options = {"backend": "browser", "url": expected_urls[state + "_start"]}
                if browser_executable:
                    options["browser_executable"] = browser_executable
                opened = service.session_open(**options)
                # Wait using the public recognition-only probe, not app internals or setup inputs.
                deadline = time.monotonic() + 10
                node = spec["scenarios"][state + "_start"]
                while True:
                    observation = service.observe(opened["session_id"], ocr=False)
                    if service.probe(opened["session_id"], observation["observation_id"], node)[
                        "result"
                    ]["hit"]:
                        break
                    if time.monotonic() >= deadline:
                        raise ValueError(f"Starting UI did not become ready: {state}")
                    time.sleep(0.1)
                result = manager.run(
                    service, opened["session_id"], "AgentCanvas", scenario=state + "_start"
                )
                attempt = result["attempt"]
                attempts.append(attempt)
                if attempt.get("run_id"):
                    Reports(project).export_run(
                        attempt["run_id"], str(output / f"evidence-{attempt['run_id']}.zip")
                    )
            finally:
                service.close()
    # A new manager after all browser workers close must recover the same current proofs.
    restored = TaskManager(project).context("AgentCanvas")
    after = source_hashes(project)
    result = {
        "schema_version": 1,
        "mode": "durable_task_validation_no_model_calls",
        "passed": all(attempt["accepted"] for attempt in attempts)
        and len(attempts) == 2
        and restored["state"] == "accepted"
        and before == after,
        "source_unchanged": before == after,
        "source_hashes": before,
        "installed_from": str(Path(vibe_maa.__file__).resolve()),
        "upstream": upstream,
        "attempts": attempts,
        "restored_context": restored,
    }
    write_result(output / "task-validation.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", default=Path("work/targets"), type=Path)
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    result = validate(args.project, args.output, args.cache, args.browser_executable)
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "state": result["restored_context"]["state"],
                "source_unchanged": result["source_unchanged"],
            }
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
