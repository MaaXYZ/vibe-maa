"""Independent acceptance checks. This module is outside the agent's writable project."""

from __future__ import annotations

import contextlib
import functools
import http.server
import importlib.util
import json
import threading
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import numpy as np

from vibe_maa.errors import DevError
from vibe_maa.evidence import read_image
from vibe_maa.service import DevelopmentService, load_nodes

ROOT = Path(__file__).resolve().parents[2]
ENTRY = "AgentTask"
ALLOWED_ACTIONS = {"DoNothing", "Click", "LongPress", "Swipe", "ClickKey", "InputText"}
DRAWIO_STARTING_STATES = ("blank", "splash")


def drawio_start_url(url: str, starting_state: str) -> str:
    """Select an upstream startup screen without changing the application bytes."""
    if starting_state not in DRAWIO_STARTING_STATES:
        raise ValueError(f"Unknown draw.io starting state: {starting_state}")
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query))
    query["splash"] = "1" if starting_state == "splash" else "0"
    return urlunsplit(parsed._replace(query=urlencode(query)))


def validate_submission(project: Path) -> None:
    if any((project / "resource").rglob("default_pipeline.json")):
        raise DevError(
            "INVALID_SUBMISSION",
            "This benchmark requires explicit nodes; default_pipeline.json is not allowed",
        )
    nodes = load_nodes(project)
    if ENTRY not in nodes:
        raise DevError("INVALID_SUBMISSION", f"Deliver a standard Pipeline with entry {ENTRY}")
    for name, node in nodes.items():
        action = node.get("action", "DoNothing")
        kind = action.get("type") if isinstance(action, dict) else action
        if kind not in ALLOWED_ACTIONS:
            raise DevError("INVALID_SUBMISSION", f"Disallowed acceptance action in {name}: {kind}")
        recognition = node.get("recognition", "DirectHit")
        kind = recognition.get("type") if isinstance(recognition, dict) else recognition
        if kind == "Custom":
            raise DevError("INVALID_SUBMISSION", "Custom handlers are outside this benchmark")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextlib.contextmanager
def drawio_server(cache: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    source = ROOT / "integration/targets/drawio/prepare.py"
    spec = importlib.util.spec_from_file_location("vibe_drawio_prepare", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("draw.io provisioner is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    webroot = module.provision(cache)
    metadata = module.manifest()
    handler = functools.partial(_QuietHandler, directory=str(webroot))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{metadata['launch_path']}", metadata
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def grade_mailbox(project: Path) -> dict[str, Any]:
    validate_submission(project)
    checks = []
    for state in ["home", "mailbox", "popup"]:
        service = DevelopmentService(project)
        try:
            opened = service.session_open(backend="demo", initial_state=state)
            report = service.run(opened["session_id"], ENTRY, timeout_seconds=20)
            backend = service.sessions[opened["session_id"]].backend
            paths = [e["image_path"] for e in report["events"] if e.get("image_path")]
            visited_mailbox = any(
                np.array_equal(read_image(Path(p))[0, 0], [40] * 3) for p in paths
            )
            final_home = backend.world.state == "home"
            passed = report["execution_status"] == "succeeded" and final_home and visited_mailbox
            checks.append(
                {
                    "starting_state": state,
                    "execution_status": report["execution_status"],
                    "visited_mailbox": visited_mailbox,
                    "returned_home": final_home,
                    "passed": passed,
                    "run_id": report["run_id"],
                }
            )
        finally:
            service.close()
    return {
        "passed": all(c["passed"] for c in checks),
        "target": "mailbox",
        "scope": "fixture",
        "checks": checks,
        "oracle": "fresh native fixture state plus original recognition frames",
    }


@contextlib.contextmanager
def _drawio_oracle(url: str) -> Iterator[list[str]]:
    """Test-only private DOM oracle. No DOM operation is added to the product API."""
    from playwright.sync_api import Browser

    from vibe_maa.backends.browser import _BrowserWorker

    original = _BrowserWorker._dispatch
    original_context = Browser.new_context
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    blocked = []

    def context(browser: Any, *args: Any, **kwargs: Any) -> Any:
        kwargs["service_workers"] = "block"
        instance = original_context(browser, *args, **kwargs)
        instance.add_init_script("""
            window.addEventListener('load', () => {
                const init = App.prototype.init;
                App.prototype.init = function() {
                    window.__vibe_grader_editor = this;
                    return init.apply(this, arguments);
                };
            }, {once: true});
        """)

        def route(request: Any) -> None:
            target = urlsplit(request.request.url)
            if f"{target.scheme}://{target.netloc}" == origin or target.scheme in {"data", "blob"}:
                request.continue_()
            else:
                blocked.append(request.request.url)
                request.abort()

        instance.route("**/*", route)
        return instance

    def dispatch(page: Any, operation: str, args: tuple[Any, ...]) -> Any:
        if operation == "__grader_ready":
            starting_state = args[0] if args else "blank"
            page.wait_for_function(
                """state => {
                    const app = window.__vibe_grader_editor;
                    if (!app) return false;
                    const dialogs = document.querySelectorAll('.geDialog').length;
                    return state === 'splash'
                        ? !app.getCurrentFile() && dialogs === 1
                        : app.getCurrentFile() && app.editor.graph.isEnabled() && dialogs === 0;
                }""",
                arg=starting_state,
                timeout=10000,
            )
            return True
        if operation == "__grader_state":
            return page.evaluate("""() => ({
                current_file: !!window.__vibe_grader_editor.getCurrentFile(),
                graph_enabled: window.__vibe_grader_editor.editor.graph.isEnabled(),
                dialog_count: document.querySelectorAll('.geDialog').length
            })""")
        if operation == "__grader_document":
            return page.evaluate("""() => {
                if (!window.__vibe_grader_editor.getCurrentFile()) return [];
                const graph = window.__vibe_grader_editor.editor.graph;
                return Object.values(graph.model.cells).filter(cell => cell.vertex).map(cell => ({
                    label: graph.convertValueToString(cell), style: cell.style || '',
                    shape: graph.getCellStyle(cell).shape,
                    rectangle_renderer: graph.view.getState(cell).shape instanceof mxRectangleShape,
                    ellipse_renderer: graph.view.getState(cell).shape instanceof mxEllipse,
                    width: cell.geometry.width, height: cell.geometry.height
                }));
            }""")
        return original(page, operation, args)

    _BrowserWorker._dispatch = staticmethod(dispatch)
    Browser.new_context = context
    try:
        yield blocked
    finally:
        _BrowserWorker._dispatch = staticmethod(original)
        Browser.new_context = original_context


def grade_drawio(
    project: Path,
    url: str,
    browser_executable: str | None = None,
    *,
    starting_states: tuple[str, ...] = ("blank",),
    repetitions: int = 2,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    if not starting_states or not 1 <= repetitions <= 10:
        raise ValueError("Specify starting states and between 1 and 10 repetitions")
    launches = [(state, drawio_start_url(url, state)) for state in starting_states]
    validate_submission(project)
    checks = []
    with _drawio_oracle(url) as blocked:
        for starting_state, launch in launches:
            for repetition in range(repetitions):
                checks.append(
                    _grade_drawio_once(
                        project,
                        launch,
                        browser_executable,
                        starting_state,
                        repetition,
                        timeout_seconds,
                    )
                )
    return {
        "passed": all(c["passed"] for c in checks) and not blocked,
        "target": "drawio",
        "scope": "application",
        "starting_states": list(starting_states),
        "repetitions_per_state": repetitions,
        "checks": checks,
        "blocked_external_requests": blocked,
        "oracle": "fresh real application canvas DOM, independent from agent-supplied goals",
    }


def _grade_drawio_once(
    project: Path,
    url: str,
    browser_executable: str | None,
    starting_state: str,
    repetition: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    service = DevelopmentService(project)
    try:
        options = {"backend": "browser", "url": url}
        if browser_executable:
            options["browser_executable"] = browser_executable
        opened = service.session_open(**options)
        backend = service.sessions[opened["session_id"]].backend
        backend._worker.call("__grader_ready", starting_state)
        initial_state = backend._worker.call("__grader_state")
        before = backend._worker.call("__grader_document")
        report = service.run(opened["session_id"], ENTRY, timeout_seconds=timeout_seconds)
        document = backend._worker.call("__grader_document")
        final_state = backend._worker.call("__grader_state")
        changed = completed_drawio_document(before, document)
        return {
            "starting_state": starting_state,
            "repetition": repetition + 1,
            "initial_state": initial_state,
            "final_state": final_state,
            "execution_status": report["execution_status"],
            "label_in_canvas": changed,
            "document": document,
            "run_id": report["run_id"],
            "passed": report["execution_status"] == "succeeded"
            and changed
            and final_state["current_file"]
            and final_state["graph_enabled"]
            and final_state["dialog_count"] == 0,
        }
    finally:
        service.close()


def completed_drawio_document(before: list[dict[str, Any]], document: list[dict[str, Any]]) -> bool:
    # draw.io's default rectangular palette shape uses mxLabel, a subclass of mxRectangleShape.
    # The style ID is "label"; comparing it to "rectangle" falsely rejects the canonical shape.
    return (
        not before
        and len(document) == 1
        and document[0]["label"] == "Vibe Maa"
        and document[0].get("rectangle_renderer") is True
        and document[0].get("ellipse_renderer") is False
        and document[0]["width"] > 0
        and document[0]["height"] > 0
    )


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
