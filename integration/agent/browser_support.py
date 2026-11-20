"""Private browser infrastructure shared by the application acceptance targets."""

from __future__ import annotations

import contextlib
import functools
import http.server
import io
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BrowserTarget:
    objective: str
    oracle: str
    launch: Callable[[Path, str], AbstractContextManager[tuple[str, dict[str, Any]]]]
    ready: Callable[[Any], None]
    snapshot: Callable[[Any], Any]
    completed: Callable[[Any, Any], bool]
    init_script: str | None = None
    variants: tuple[str, ...] = ("first", "second")


@contextlib.contextmanager
def serve_directory(
    directory: Path, *, assets: dict[str, tuple[str, bytes]] | None = None
) -> Iterator[str]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = {
            **http.server.SimpleHTTPRequestHandler.extensions_map,
            ".mjs": "text/javascript",
        }

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def send_head(self):
            asset = (assets or {}).get(urlsplit(self.path).path)
            if asset is None:
                return super().send_head()
            content_type, content = asset
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            return io.BytesIO(content)

    handler = functools.partial(Handler, directory=str(directory))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


@contextlib.contextmanager
def browser_oracle(url: str, target: BrowserTarget) -> Iterator[list[str]]:
    """Observe application state on its browser owner thread, outside the agent API."""
    from playwright.sync_api import Browser

    from vibe_maa.backends.browser import _BrowserWorker

    original_dispatch = _BrowserWorker._dispatch
    original_context = Browser.new_context
    origin = urlsplit(url)
    blocked = []

    def context(browser: Any, *args: Any, **kwargs: Any) -> Any:
        instance = original_context(browser, *args, **kwargs, service_workers="block")
        if target.init_script:
            instance.add_init_script(target.init_script)

        def route(request: Any) -> None:
            address = urlsplit(request.request.url)
            if (address.scheme, address.netloc) == (origin.scheme, origin.netloc) or (
                address.scheme in {"data", "blob"}
            ):
                request.continue_()
            else:
                blocked.append(request.request.url)
                request.abort()

        def route_web_socket(socket: Any) -> None:
            address = urlsplit(socket.url)
            scheme = "wss" if origin.scheme == "https" else "ws"
            if (address.scheme, address.netloc) == (scheme, origin.netloc):
                socket.connect_to_server()
            else:
                blocked.append(socket.url)
                socket.close()

        instance.route("**/*", route)
        instance.route_web_socket("**/*", route_web_socket)
        return instance

    def dispatch(page: Any, operation: str, args: tuple[Any, ...]) -> Any:
        if operation == "__grader_ready":
            return target.ready(page)
        if operation == "__grader_snapshot":
            return target.snapshot(page)
        return original_dispatch(page, operation, args)

    _BrowserWorker._dispatch = staticmethod(dispatch)
    Browser.new_context = context
    try:
        yield blocked
    finally:
        _BrowserWorker._dispatch = staticmethod(original_dispatch)
        Browser.new_context = original_context
