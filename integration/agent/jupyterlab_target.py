"""Pinned JupyterLab workspace and independent persisted-file oracle."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from browser_support import BrowserTarget

VERSION = "4.6.6"
FILENAME = "release-notes.md"
CONTENT = "# Vibe Maa\nAutomation verified."
EXISTING_FILENAME = "project-notes.txt"
EXISTING_CONTENT = "Existing project notes.\n"
OBJECTIVE = (
    f"In JupyterLab, create one Markdown file named {FILENAME} in the workspace root. "
    "A workspace may already have project notes. Preserve them. "
    "Use the file editor to write these two lines and save the new file:\n"
    f"{CONTENT}\n"
)
ORACLE = "fresh JupyterLab workspace and the exact Markdown saved through its Contents API"

# Block code execution in the target service. An empty kernel list alone still lets Jupyter
# resolve its built-in Python kernel by name. This workspace only needs the document editor.
SERVER_CONFIG = """
from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel

class NoKernels(KernelSpecManager):
    def find_kernel_specs(self):
        return {}

    def get_kernel_spec(self, kernel_name):
        raise NoSuchKernel(kernel_name)

c.ServerApp.kernel_spec_manager_class = NoKernels
c.ServerApp.terminals_enabled = False
c.ServerApp.jpserver_extensions = {
    "jupyterlab": True,
    "jupyter_lsp": False,
    "jupyter_server_terminals": False,
}
c.IdentityProvider.token = ""
c.LabApp.extension_manager = "readonly"
c.LabApp.news_url = None
c.LabApp.check_for_updates_class = "jupyterlab.handlers.announcements.NeverCheckForUpdate"
"""


def _wait_for_port(process: subprocess.Popen, runtime: Path, log: Path) -> int:
    info = runtime / f"jpserver-{process.pid}.json"
    deadline = time.monotonic() + 30
    while process.poll() is None and time.monotonic() < deadline:
        try:
            port = json.loads(info.read_text(encoding="utf-8"))["port"]
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.1)
        else:
            return port
    startup_log = log.read_text(encoding="utf-8", errors="replace")[-4000:]
    raise RuntimeError(f"JupyterLab failed to start.\n{startup_log}")


@contextlib.contextmanager
def launch(cache: Path, variant: str = "empty-workspace") -> Iterator[tuple[str, dict[str, Any]]]:
    target = cache.resolve() / "jupyterlab"
    python = target / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise RuntimeError(
            f"Install the locked JupyterLab environment with `uv venv --python 3.12 "
            f"{target / 'venv'}` and `uv pip sync --python {target / 'venv'} "
            "integration/agent/jupyterlab.requirements.txt`."
        )
    with tempfile.TemporaryDirectory(prefix="session-", dir=target) as directory:
        state = Path(directory)
        workspace = state / "workspace"
        workspace.mkdir()
        if variant == "existing-notes":
            (workspace / EXISTING_FILENAME).write_text(EXISTING_CONTENT, encoding="utf-8")
        settings = state / "settings"
        notifications = settings / "@jupyterlab/apputils-extension/notification.jupyterlab-settings"
        notifications.parent.mkdir(parents=True)
        notifications.write_text('{"fetchNews":"false"}', encoding="utf-8")
        config = state / "jupyter_server_config.py"
        config.write_text(SERVER_CONFIG, encoding="utf-8")
        runtime = state / "runtime"
        log = state / "server.log"
        environment = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
            if name in os.environ
        }
        environment.update(
            JUPYTER_CONFIG_DIR=str(state / "config"),
            JUPYTER_DATA_DIR=str(state / "data"),
            JUPYTER_RUNTIME_DIR=str(runtime),
            JUPYTERLAB_SETTINGS_DIR=str(settings),
            JUPYTERLAB_WORKSPACES_DIR=str(state / "workspaces"),
        )
        with log.open("wb") as stream:
            process = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "jupyterlab",
                    f"--config={config}",
                    "--no-browser",
                    "--ip=127.0.0.1",
                    "--port=0",
                    f"--ServerApp.root_dir={workspace}",
                ],
                cwd=workspace,
                env=environment,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
            try:
                port = _wait_for_port(process, runtime, log)
                yield (
                    f"http://127.0.0.1:{port}/lab",
                    {
                        "name": "JupyterLab",
                        "version": VERSION,
                        "source": f"https://github.com/jupyterlab/jupyterlab/releases/tag/v{VERSION}",
                        "python_requirements": "integration/agent/jupyterlab.requirements.txt",
                    },
                )
            finally:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def ready(page: Any) -> None:
    page.get_by_role("button", name="Markdown File").wait_for()


def snapshot(page: Any) -> dict[str, Any]:
    root = page.request.get(urljoin(page.url, "/api/contents?content=1"), timeout=5000)
    if not root.ok:
        raise RuntimeError(f"JupyterLab Contents API returned HTTP {root.status}")
    files = dict.fromkeys(sorted(item["path"] for item in root.json()["content"]))
    for filename in (FILENAME, EXISTING_FILENAME):
        if filename not in files:
            continue
        file = page.request.get(
            urljoin(page.url, f"/api/contents/{filename}?content=1"), timeout=5000
        )
        if not file.ok:
            raise RuntimeError(f"JupyterLab could not read {filename}: HTTP {file.status}")
        model = file.json()
        if model["type"] == "file" and model["format"] == "text":
            files[filename] = model["content"]
    return files


def completed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return (
        before in ({}, {EXISTING_FILENAME: EXISTING_CONTENT})
        and after.get(FILENAME) in (CONTENT, f"{CONTENT}\n")
        and {name: content for name, content in after.items() if name != FILENAME} == before
    )


TARGET = BrowserTarget(
    objective=OBJECTIVE,
    oracle=ORACLE,
    launch=launch,
    ready=ready,
    snapshot=snapshot,
    completed=completed,
    variants=("empty-workspace", "existing-notes"),
)
