"""Real, pinned diagrams.net acceptance through MaaFW's pixel/input controller.

The only DOM/model access below is an independent test oracle installed by the
harness. Neither the product protocol nor the coding agent can call it.
"""

import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Browser

from vibe_maa.backends.browser import BrowserBackend, _BrowserWorker
from vibe_maa.evidence import save_image
from vibe_maa.service import DevelopmentService, Session
from vibe_maa.targets.drawio.prepare import manifest, provision

pytestmark = pytest.mark.browser
LABEL = "Vibe Maa"

ORACLE_HOOK = """
window.addEventListener('load', () => {
    const init = App.prototype.init;
    App.prototype.init = function() {
        window.__vibe_ci_editor = this;
        return init.apply(this, arguments);
    };
}, {once: true});
"""


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def target_server():
    cache = Path(os.environ.get("VIBE_MAA_DRAWIO_CACHE", "work/targets"))
    webroot = provision(cache)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(webroot)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        yield origin, origin + manifest()["launch_path"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def independent_oracle(monkeypatch, target_server):
    origin, _ = target_server
    blocked = []
    original_context = Browser.new_context
    original_dispatch = _BrowserWorker._dispatch

    def local_context(browser, *args, **kwargs):
        kwargs["service_workers"] = "block"
        context = original_context(browser, *args, **kwargs)
        context.add_init_script(ORACLE_HOOK)

        def route(request):
            parsed = urlsplit(request.request.url)
            request_origin = f"{parsed.scheme}://{parsed.netloc}"
            if request_origin == origin or parsed.scheme in {"data", "blob"}:
                request.continue_()
            else:
                blocked.append(request.request.url)
                request.abort()

        context.route("**/*", route)
        return context

    def oracle_dispatch(page, operation, args):
        if operation == "__ci_ready":
            page.wait_for_function(
                "window.__vibe_ci_editor && __vibe_ci_editor.getCurrentFile() && "
                "__vibe_ci_editor.editor.graph.isEnabled()",
                timeout=10000,
            )
            return True
        if operation == "__ci_document":
            return page.evaluate("""() => {
                const graph = window.__vibe_ci_editor.editor.graph;
                return Object.values(graph.model.cells).filter(cell => cell.vertex).map(cell => {
                    const state = graph.view.getState(cell);
                    const bounds = state.shape.node.getBoundingClientRect();
                    return {label: graph.convertValueToString(cell),
                        box: [Math.floor(bounds.x), Math.floor(bounds.y),
                            Math.ceil(bounds.width), Math.ceil(bounds.height)]};
                });
            }""")
        return original_dispatch(page, operation, args)

    monkeypatch.setattr(Browser, "new_context", local_context)
    monkeypatch.setattr(_BrowserWorker, "_dispatch", staticmethod(oracle_dispatch))
    return blocked


def _backend(project, url):
    backend = BrowserBackend(
        project,
        url,
        executable_path=os.environ.get("VIBE_MAA_BROWSER_EXECUTABLE"),
    )
    backend._worker.call("__ci_ready")
    return backend


def _nodes():
    return {
        "CreateDiagram": {"action": "DoNothing", "next": ["InsertRectangle"]},
        "InsertRectangle": {
            "recognition": "TemplateMatch",
            "template": "RectangleButton.png",
            "roi": [18, 162, 38, 36],
            "threshold": 0.99,
            "action": "Click",
            "post_delay": 250,
            "next": ["SelectRectangle"],
        },
        "SelectRectangle": {
            "action": "Click",
            "target": [630, 420, 1, 1],
            "post_delay": 100,
            "next": ["EditLabel"],
        },
        "EditLabel": {
            "action": "ClickKey",
            "key": 113,
            "post_delay": 100,
            "next": ["WriteLabel"],
        },
        "WriteLabel": {
            "action": "InputText",
            "input_text": LABEL,
            "post_delay": 100,
            "next": ["FinishEditing"],
        },
        "FinishEditing": {"action": "ClickKey", "key": 27, "post_delay": 100},
    }


@pytest.fixture
def project(tmp_path, target_server, independent_oracle):
    """Prepare independently verified reference images outside the tested run."""
    _, url = target_server
    pipeline = tmp_path / "resource" / "pipeline"
    images = tmp_path / "resource" / "image"
    pipeline.mkdir(parents=True)
    images.mkdir()
    nodes = _nodes()
    path = pipeline / "drawio.json"
    path.write_text(json.dumps(nodes), encoding="utf-8")
    reference = _backend(tmp_path, url)
    try:
        blank = reference.capture()
        save_image(images / "RectangleButton.png", blank[162:198, 18:56])
        result = reference.run("CreateDiagram", {}, 15, lambda *_: None)
        assert result["execution_status"] == "succeeded"
        document = reference._worker.call("__ci_document")
        assert len(document) == 1 and document[0]["label"] == LABEL
        x, y, width, height = document[0]["box"]
        assert 8 <= x < 1280 - width - 8 and 8 <= y < 720 - height - 8, document
        box = [x - 8, y - 8, width + 16, height + 16]
        image = reference.capture()
        save_image(
            images / "VerifiedNamedShape.png", image[y - 8 : y + height + 8, x - 8 : x + width + 8]
        )
        nodes["NamedShapeVisible"] = {
            "recognition": "TemplateMatch",
            "template": "VerifiedNamedShape.png",
            "roi": box,
            "threshold": 0.99,
            "action": "DoNothing",
        }
        path.write_text(json.dumps(nodes, indent=2), encoding="utf-8")
        (tmp_path / "reference-oracle.json").write_text(
            json.dumps({"upstream": manifest(), "document": document, "goal_box": box}, indent=2),
            encoding="utf-8",
        )
    finally:
        reference.close()
    return tmp_path


@pytest.mark.parametrize("missing_label", [False, True], ids=["complete", "missing-label"])
def test_native_pipeline_matches_independent_drawio_result(
    project, target_server, independent_oracle, missing_label
):
    _, url = target_server
    backend = _backend(project, url)
    service = DevelopmentService(project)
    service.sessions["drawio_ci"] = Session("drawio_ci", backend, "browser", url, "resource")
    try:
        blank = backend.capture()
        goal = json.loads((project / "resource/pipeline/drawio.json").read_text())[
            "NamedShapeVisible"
        ]
        assert backend.recognize(blank, goal)["hit"] is False
        override = {"SelectRectangle": {"next": ["FinishEditing"]}} if missing_label else {}
        report = service.run("drawio_ci", "CreateDiagram", override, 15, "NamedShapeVisible")
        assert report["execution_status"] == "succeeded"
        document = backend._worker.call("__ci_document")
        assert len(document) == 1
        expected = "" if missing_label else LABEL
        assert document[0]["label"] == expected
        assert report["goal_status"] == ("failed" if missing_label else "passed")
        assert report["last_recognition"]["image_path"]
        assert Path(report["report_path"]).is_file()
        (project / "independent-oracle.json").write_text(
            json.dumps(
                {
                    "document": document,
                    "expected_label": expected,
                    "tasker_status": report["execution_status"],
                    "goal_status": report["goal_status"],
                    "run_id": report["run_id"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert independent_oracle == [], "The local target attempted external network access"
    finally:
        service.close()
