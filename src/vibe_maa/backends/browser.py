"""Pixel-only Chromium transport for MaaFramework's native CustomController.

All Playwright objects live on one dedicated thread. Native MaaFW callbacks and
independent daemon request threads communicate with it through a bounded queue.
No DOM selectors, accessibility tree, or JavaScript evaluation are exposed.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import io
import math
import platform
import queue
import threading
import time
from concurrent.futures import Future, TimeoutError
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np
from maa.controller import CustomController
from PIL import Image

from vibe_maa.backends.base import EventCallback
from vibe_maa.backends.maa import MaaBackend
from vibe_maa.errors import DevError


def _validate_url(url: str) -> str:
    if not isinstance(url, str):
        raise DevError("INVALID_ARGUMENT", "Browser URL must be an http(s) URL")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Expected an http(s) URL with a hostname")
        _ = parsed.port
    except ValueError as exc:
        raise DevError("INVALID_ARGUMENT", str(exc)) from exc
    if any(ord(char) < 32 for char in url):
        raise DevError("INVALID_ARGUMENT", "Browser URL contains control characters")
    return url


def _key_name(code: int) -> str:
    names = {
        8: "Backspace",
        9: "Tab",
        13: "Enter",
        16: "Shift",
        17: "Control",
        18: "Alt",
        20: "CapsLock",
        27: "Escape",
        32: "Space",
        33: "PageUp",
        34: "PageDown",
        35: "End",
        36: "Home",
        37: "ArrowLeft",
        38: "ArrowUp",
        39: "ArrowRight",
        40: "ArrowDown",
        45: "Insert",
        46: "Delete",
        91: "Meta",
    }
    if code in names:
        return names[code]
    if 48 <= code <= 57:
        return chr(code)
    if 65 <= code <= 90:
        return chr(code).lower()
    if 112 <= code <= 123:
        return f"F{code - 111}"
    raise DevError("UNSUPPORTED_BROWSER_KEY", f"Unsupported browser virtual key: {code}")


class _BrowserWorker:
    def __init__(
        self, url: str, headless: bool, width: int, height: int, executable_path: str | None
    ):
        self._queue: queue.Queue[tuple[str, tuple[Any, ...], Future]] = queue.Queue(maxsize=64)
        self._lock = threading.Lock()
        self._closed = False
        self._broken = False
        self._ready: Future = Future()
        self._thread = threading.Thread(
            target=self._serve,
            args=(url, headless, width, height, executable_path),
            name="vibe-maa-browser",
            daemon=True,
        )
        self._thread.start()
        try:
            self.runtime_info = self._ready.result(timeout=45)
        except TimeoutError as exc:
            # If startup eventually returns, this sentinel still closes its browser.
            self._queue.put_nowait(("close", (), Future()))
            self._closed = True
            raise DevError("BROWSER_START_TIMEOUT", "Chromium startup exceeded 45 seconds") from exc

    def _serve(
        self, url: str, headless: bool, width: int, height: int, executable_path: str | None
    ) -> None:
        playwright, browser = None, None
        try:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise DevError(
                    "BROWSER_UNAVAILABLE",
                    "Install the browser extra: pip install 'vibe-maa[browser]', then "
                    "python -m playwright install chromium",
                ) from exc
            playwright = sync_playwright().start()
            options: dict[str, Any] = {"headless": headless}
            if executable_path is not None:
                options["executable_path"] = executable_path
            try:
                browser = playwright.chromium.launch(**options)
            except Exception as exc:
                raise DevError(
                    "BROWSER_UNAVAILABLE",
                    "Cannot launch Chromium. Run python -m playwright install chromium, or "
                    f"provide browser_executable for an installed Chromium. Details: {exc}",
                ) from exc
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
                accept_downloads=False,
            )
            page = context.new_page()
            page.set_default_timeout(10000)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                raise DevError("BROWSER_NAVIGATION_FAILED", f"Cannot open {url}: {exc}") from exc
            self._ready.set_result({"browser_version": browser.version})
            while True:
                operation, args, future = self._queue.get()
                if operation == "close":
                    browser.close()
                    browser = None
                    future.set_result(True)
                    return
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(self._dispatch(page, operation, args))
                except Exception as exc:
                    future.set_exception(
                        exc
                        if isinstance(exc, DevError)
                        else DevError("BROWSER_OPERATION_FAILED", f"{operation}: {exc}")
                    )
        except BaseException as exc:
            error = exc if isinstance(exc, DevError) else DevError("BROWSER_FAILED", str(exc))
            if not self._ready.done():
                self._ready.set_exception(error)
            with self._lock:
                self._broken = True
            while True:
                try:
                    _, _, future = self._queue.get_nowait()
                except queue.Empty:
                    break
                if not future.done():
                    future.set_exception(error)
        finally:
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.close()
            if playwright is not None:
                with contextlib.suppress(Exception):
                    playwright.stop()

    @staticmethod
    def _dispatch(page: Any, operation: str, args: tuple[Any, ...]) -> Any:
        if operation == "capture":
            png = page.screenshot(type="png", full_page=False, timeout=10000)
            with Image.open(io.BytesIO(png)) as image:
                return np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
        if operation == "click":
            page.mouse.click(*args)
        elif operation == "swipe":
            x1, y1, x2, y2, duration = args
            steps = max(1, min(120, math.ceil(duration / 16)))
            page.mouse.move(x1, y1)
            page.mouse.down()
            started = time.monotonic()
            try:
                for step in range(1, steps + 1):
                    page.mouse.move(x1 + (x2 - x1) * step / steps, y1 + (y2 - y1) * step / steps)
                    remaining = started + duration / 1000 * step / steps - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
            finally:
                page.mouse.up()
        elif operation == "key":
            page.keyboard.press(_key_name(args[0]))
        elif operation == "key_down":
            page.keyboard.down(_key_name(args[0]))
        elif operation == "key_up":
            page.keyboard.up(_key_name(args[0]))
        elif operation == "text":
            page.keyboard.insert_text(args[0])
        elif operation == "scroll":
            page.mouse.wheel(*args)
        else:
            raise DevError("INVALID_ACTION", f"Unknown browser operation: {operation}")
        return True

    def call(self, operation: str, *args: Any) -> Any:
        with self._lock:
            if self._closed or self._broken:
                raise DevError("BROWSER_CLOSED", "Browser transport is closed or unusable")
            future: Future = Future()
            try:
                self._queue.put_nowait((operation, args, future))
            except queue.Full as exc:
                raise DevError("BROWSER_BUSY", "Browser operation queue is full") from exc
        try:
            return future.result(timeout=14)
        except TimeoutError as exc:
            future.cancel()
            with self._lock:
                self._broken = True
            raise DevError(
                "BROWSER_TIMEOUT",
                "Browser did not answer before its deadline; the input may have taken effect. "
                "Close this session before reconnecting.",
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if not self._thread.is_alive():
                return
            future: Future = Future()
            try:
                self._queue.put_nowait(("close", (), future))
            except queue.Full as exc:
                raise DevError("BROWSER_CLOSE_FAILED", "Browser queue is full") from exc
        try:
            future.result(timeout=15)
        except TimeoutError as exc:
            raise DevError("BROWSER_CLOSE_TIMEOUT", "Browser did not finish shutting down") from exc
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise DevError("BROWSER_CLOSE_TIMEOUT", "Browser owner thread did not finish")


class BrowserController(CustomController):
    """Native input/screenshot transport; all browser state stays on its owner thread."""

    def __init__(self, worker: _BrowserWorker, url: str):
        self.worker = worker
        self.url = url
        self._error_lock = threading.Lock()
        self._error: DevError | None = None
        super().__init__()

    def _call(self, operation: str, *args: Any) -> Any:
        try:
            return self.worker.call(operation, *args)
        except DevError as exc:
            with self._error_lock:
                self._error = exc
            return None

    def pop_error(self) -> DevError | None:
        with self._error_lock:
            error, self._error = self._error, None
            return error

    def connect(self) -> bool:
        return True

    def request_uuid(self) -> str:
        return f"vibe-maa-browser:{self.url}"

    def get_features(self) -> int:
        return 0

    def start_app(self, intent: str) -> bool:
        return False

    def stop_app(self, intent: str) -> bool:
        return False

    def screencap(self) -> np.ndarray:
        image = self._call("capture")
        return image if image is not None else np.empty((0, 0, 3), dtype=np.uint8)

    def click(self, x: int, y: int) -> bool:
        return bool(self._call("click", x, y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
        return bool(self._call("swipe", x1, y1, x2, y2, duration))

    def touch_down(self, contact: int, x: int, y: int, pressure: int) -> bool:
        return False

    def touch_move(self, contact: int, x: int, y: int, pressure: int) -> bool:
        return False

    def touch_up(self, contact: int) -> bool:
        return False

    def click_key(self, keycode: int) -> bool:
        return bool(self._call("key", keycode))

    def input_text(self, text: str) -> bool:
        return bool(self._call("text", text))

    def key_down(self, keycode: int) -> bool:
        return bool(self._call("key_down", keycode))

    def key_up(self, keycode: int) -> bool:
        return bool(self._call("key_up", keycode))

    def scroll(self, dx: int, dy: int) -> bool:
        return bool(self._call("scroll", dx, dy))


class BrowserBackend(MaaBackend):
    """A real Chromium page controlled exclusively through pixels and native MaaFW."""

    def __init__(
        self,
        project: Path,
        url: str,
        headless: bool = True,
        width: int = 1280,
        height: int = 720,
        resource_path: str = "resource",
        *,
        executable_path: str | None = None,
    ):
        url = _validate_url(url)
        if type(headless) is not bool:
            raise DevError("INVALID_ARGUMENT", "headless must be a boolean")
        if any(type(n) is not int or not 64 <= n <= 4096 for n in (width, height)):
            raise DevError("INVALID_ARGUMENT", "Viewport dimensions must be integers in [64,4096]")
        project = Path(project).resolve()
        resource = (project / resource_path).resolve()
        if not resource.is_relative_to(project) or not resource.is_dir():
            raise DevError("INVALID_RESOURCE", "Resource directory must exist inside the project")
        self.url = url
        self.width, self.height = width, height
        self._worker = _BrowserWorker(url, headless, width, height, executable_path)
        self.runtime_info = {
            **self._worker.runtime_info,
            "playwright_version": importlib.metadata.version("playwright"),
            "viewport": {"width": width, "height": height, "device_scale_factor": 1},
            "headless": headless,
            "platform": platform.system(),
        }
        try:
            controller = BrowserController(self._worker, url)
            super().__init__(
                project,
                device=url,
                resource_path=resource_path,
                controller=controller,
                target_short_side=min(width, height),
            )
            if not controller.set_screenshot_use_raw_size(True):
                raise DevError("DEVICE_ERROR", "Cannot preserve browser viewport coordinates")
        except BaseException:
            with contextlib.suppress(Exception):
                self._worker.close()
            raise

    def capture(self) -> np.ndarray:
        self.controller.pop_error()
        try:
            return super().capture()
        except DevError:
            error = self.controller.pop_error()
            if error is not None:
                raise error
            raise

    def act(self, action: dict[str, Any]) -> bool:
        self.controller.pop_error()
        try:
            return super().act(action)
        except DevError:
            error = self.controller.pop_error()
            if error is not None:
                raise error
            raise

    def run(
        self, entry: str, override: dict[str, Any], timeout_seconds: float, on_event: EventCallback
    ) -> dict[str, Any]:
        self.controller.pop_error()
        result = super().run(entry, override, timeout_seconds, on_event)
        error = self.controller.pop_error()
        if error is not None:
            result["transport_error"] = error.as_dict()
        return result

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._worker.close()
