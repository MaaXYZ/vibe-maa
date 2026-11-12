"""Native MaaFW/Chromium transport tests; the fixture only exercises input and pixels.

The separately provisioned draw.io suite tests an actual upstream application.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from vibe_maa.backends.browser import BrowserBackend
from vibe_maa.errors import DevError

pytestmark = pytest.mark.browser

HTML = b"""<!doctype html><html><head><style>
body{margin:0;background:white}button{position:absolute;left:20px;top:20px;width:100px;height:50px}
input{position:absolute;left:20px;top:100px;width:150px;height:40px}
#drag{position:absolute;left:220px;top:200px;width:150px;height:100px;background:gray}
</style></head><body>
<button onclick="document.body.style.backgroundColor='rgb(0,255,0)'">Change</button>
<input oninput="if(this.value==='hello')document.body.style.backgroundColor='rgb(255,0,0)'">
<div id="drag"></div><script>
document.addEventListener('keydown',e=>{if(e.key==='Escape')document.body.style.backgroundColor='rgb(0,0,255)'});
let start=null;
document.getElementById('drag').addEventListener('pointerdown',e=>start=e.clientX);
document.addEventListener('pointerup',e=>{if(start!==null&&e.clientX-start>20)document.body.style.backgroundColor='rgb(128,0,128)';start=null});
</script></body></html>"""


@pytest.fixture
def page_url():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.fixture
def browser(tmp_path, page_url):
    pytest.importorskip("playwright.sync_api", reason="Install vibe-maa[browser]")
    resource = tmp_path / "resource" / "pipeline"
    resource.mkdir(parents=True)
    (resource / "test.json").write_text('{"Entry": {}}')
    executable = os.environ.get("VIBE_MAA_BROWSER_EXECUTABLE") or shutil.which("chromium")
    try:
        backend = BrowserBackend(
            tmp_path, page_url, width=640, height=360, executable_path=executable
        )
    except DevError as exc:
        if exc.code == "BROWSER_UNAVAILABLE" and not os.environ.get("CI"):
            pytest.skip(str(exc))
        raise
    yield backend, resource
    backend.close()


def other_thread(function, *args):
    result = Future()

    def invoke():
        try:
            result.set_result(function(*args))
        except BaseException as exc:
            result.set_exception(exc)

    thread = threading.Thread(target=invoke)
    thread.start()
    try:
        return result.result(timeout=20)
    finally:
        thread.join(timeout=20)


def test_pixel_capture_and_click_from_different_request_threads(browser):
    backend, _ = browser
    frame = other_thread(backend.capture)
    assert frame.shape == (360, 640, 3)
    assert np.array_equal(frame[0, 0], [255, 255, 255])
    assert other_thread(backend.act, {"type": "click", "x": 60, "y": 45})
    assert np.array_equal(other_thread(backend.capture)[0, 0], [0, 255, 0])


def test_keyboard_text_and_swipe_are_real_inputs(browser):
    backend, _ = browser
    backend.act({"type": "click", "x": 60, "y": 120})
    assert backend.act({"type": "text", "text": "hello"})
    assert np.array_equal(backend.capture()[0, 0], [0, 0, 255])  # BGR red
    assert backend.act({"type": "key", "key": 27})
    assert np.array_equal(backend.capture()[0, 0], [255, 0, 0])  # BGR blue
    assert backend.act(
        {"type": "swipe", "x1": 240, "y1": 220, "x2": 300, "y2": 220, "duration": 100}
    )
    assert np.array_equal(backend.capture()[0, 0], [128, 0, 128])
    with pytest.raises(DevError) as exc:
        backend.act({"type": "key", "key": 99999})
    assert exc.value.code == "UNSUPPORTED_BROWSER_KEY"


def test_real_native_pipeline_uses_browser_transport(browser):
    backend, resource = browser
    (resource / "test.json").write_text(
        json.dumps(
            {
                "Entry": {
                    "action": "Click",
                    "target": [20, 20, 100, 50],
                    "next": ["Green"],
                    "pre_delay": 0,
                    "post_delay": 0,
                },
                "Green": {
                    "recognition": "ColorMatch",
                    "lower": [0, 255, 0],
                    "upper": [0, 255, 0],
                    "roi": [0, 0, 5, 5],
                    "count": 1,
                    "pre_delay": 0,
                    "post_delay": 0,
                },
            }
        )
    )
    events = []
    result = other_thread(
        backend.run, "Entry", {}, 10, lambda event, image: events.append((event, image))
    )
    assert result["execution_status"] == "succeeded"
    assert [node["name"] for node in result["nodes"]] == ["Entry", "Green"]
    frames = [
        image
        for event, image in events
        if event["message"] == "Node.Recognition.Succeeded" and event["details"]["name"] == "Green"
    ]
    assert frames and np.array_equal(frames[-1][0, 0], [0, 255, 0])


def test_close_from_another_thread_releases_owner_and_blocks_operations(browser):
    backend, _ = browser
    other_thread(backend.close)
    assert not backend._worker._thread.is_alive()
    with pytest.raises(DevError):
        backend.capture()
    backend.close()  # Idempotent, including the fixture's final close.


@pytest.mark.parametrize(
    "url", ["file:///tmp/index.html", "javascript:alert(1)", "https://", "http://a:bad"]
)
def test_invalid_url_rejected_before_browser_start(tmp_path, url):
    with pytest.raises(DevError) as exc:
        BrowserBackend(tmp_path, url)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_missing_playwright_dependency_has_install_instructions(tmp_path, monkeypatch):
    import sys

    (tmp_path / "resource").mkdir()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(DevError) as exc:
        BrowserBackend(tmp_path, "http://localhost/")
    assert exc.value.code == "BROWSER_UNAVAILABLE"
    assert "vibe-maa[browser]" in str(exc.value)
    assert "playwright install chromium" in str(exc.value)
