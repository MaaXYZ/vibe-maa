"""A project-local, authenticated device owner shared by independent CLI calls."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .errors import DevError

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
SCHEMA_VERSION = 1

_BUILD_ID = hashlib.sha256(
    b"".join(
        p.relative_to(Path(__file__).parent).as_posix().encode() + p.read_bytes()
        for p in sorted(Path(__file__).parent.rglob("*.py"))
    )
).hexdigest()


def envelope(result: Any = None, error: DevError | None = None) -> dict[str, Any]:
    if error is not None:
        return {"schema_version": SCHEMA_VERSION, "ok": False, "error": error.as_dict()}
    return {"schema_version": SCHEMA_VERSION, "ok": True, "result": result}


@contextlib.contextmanager
def project_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """OS locks are released even if the owning process is killed."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(handle.fileno(), mode, 1)
        else:
            import fcntl

            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), flags)
        yield
    finally:
        # Closing the descriptor releases either platform's lock.
        handle.close()


def _state_path(project: Path) -> Path:
    return project / ".maa-dev" / "daemon.json"


def _read_state(project: Path) -> dict[str, Any] | None:
    try:
        state = json.loads(_state_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    port, token = state.get("port"), state.get("token")
    if type(port) is not int or not 0 < port < 65536 or not isinstance(token, str):
        return None
    return state


def _write_state(project: Path, state: dict[str, Any]) -> None:
    path = _state_path(project)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _exchange(
    state: dict[str, Any], method: str, params: dict[str, Any], timeout: float
) -> dict[str, Any]:
    try:
        packet = (
            json.dumps(
                {
                    "token": state["token"],
                    "method": method,
                    "params": params,
                    "version": __version__,
                    "build_id": _BUILD_ID,
                },
                allow_nan=False,
            ).encode()
            + b"\n"
        )
    except (ValueError, TypeError) as exc:
        raise DevError("INVALID_ARGUMENT", "Request must contain finite JSON values.") from exc
    if len(packet) > MAX_MESSAGE_BYTES:
        raise DevError("REQUEST_TOO_LARGE", "Request exceeds the 1 MiB protocol limit.")
    try:
        with socket.create_connection(("127.0.0.1", state["port"]), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(packet)
            with connection.makefile("rb") as stream:
                response = stream.readline(MAX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError) as exc:
        raise DevError(
            "DAEMON_UNAVAILABLE",
            "The daemon connection failed. The operation was not retried; inspect saved "
            "evidence before retrying an action.",
        ) from exc
    if not response or len(response) > MAX_RESPONSE_BYTES or not response.endswith(b"\n"):
        raise DevError("INVALID_RESPONSE", "Daemon returned an incomplete or oversized response.")
    try:
        decoded = json.loads(response)
    except ValueError as exc:
        raise DevError("INVALID_RESPONSE", "Daemon returned invalid JSON.") from exc
    if (
        not isinstance(decoded, dict)
        or decoded.get("schema_version") != SCHEMA_VERSION
        or type(decoded.get("ok")) is not bool
        or (decoded["ok"] and "result" not in decoded)
        or (not decoded["ok"] and not isinstance(decoded.get("error"), dict))
    ):
        raise DevError("INVALID_RESPONSE", "Daemon protocol version is incompatible.")
    return decoded


def daemon_status(project: Path) -> dict[str, Any]:
    state = _read_state(project)
    if state is None:
        return {"running": False}
    try:
        response = _exchange(state, "ping", {}, timeout=1)
    except DevError:
        return {"running": False, "stale_metadata": True}
    if not response.get("ok"):
        return {"running": False, "stale_metadata": True}
    if response["result"].get("project") != str(project.resolve()):
        return {"running": False, "stale_metadata": True}
    result = response["result"]
    return {
        "running": True,
        **result,
        "compatible": result.get("version") == __version__ and result.get("build_id") == _BUILD_ID,
    }


def ensure_daemon(project: Path) -> dict[str, Any]:
    project = project.resolve()
    directory = project / ".maa-dev"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with project_lock(directory / "daemon.start.lock"):
        status = daemon_status(project)
        if status["running"]:
            if not status.get("compatible"):
                raise DevError(
                    "DAEMON_VERSION_MISMATCH",
                    "The running service uses a different build; run daemon stop, then retry",
                )
            return _read_state(project)  # type: ignore[return-value]
        # An unresponsive existing owner must not be replaced by a second controller.
        try:
            with project_lock(directory / "daemon.owner.lock", blocking=False):
                pass
        except OSError as exc:
            raise DevError(
                "DAEMON_BUSY", "A daemon owns this project but is not responding."
            ) from exc
        log_path = directory / "daemon.log"
        options: dict[str, Any] = (
            {"start_new_session": True}
            if os.name != "nt"
            else {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            }
        )
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "vibe_maa.daemon", "--project", str(project)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                **options,
            )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if daemon_status(project)["running"]:
                return _read_state(project)  # type: ignore[return-value]
            if process.poll() is not None:
                break
            time.sleep(0.05)
        raise DevError(
            "DAEMON_START_FAILED", "Daemon did not become ready.", {"log": str(log_path)}
        )


def request(project: Path, method: str, params: dict[str, Any]) -> dict[str, Any]:
    state = ensure_daemon(project)
    timeout = 60.0
    if method in {"run", "task.run"}:
        duration = params.get("timeout_seconds", 20)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            if math.isfinite(duration) and 0 < duration <= 3600:
                timeout = max(timeout, float(duration) + 90)
    return _exchange(state, method, params, timeout)


def stop_daemon(project: Path) -> dict[str, Any]:
    state = _read_state(project)
    if state is None or not daemon_status(project)["running"]:
        return envelope({"stopped": False, "running": False})
    response = _exchange(state, "daemon.stop", {}, timeout=60)
    if not response.get("ok"):
        return response
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with project_lock(project / ".maa-dev" / "daemon.owner.lock", blocking=False):
                return response
        except OSError:
            time.sleep(0.02)
    return envelope({"stopped": False, "shutdown_requested": True, "pid": state["pid"]})


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = False
    block_on_close = True

    def __init__(self, service: Any, token: str, project: Path):
        self.service = service
        self.token = token
        self.project = project
        self.operation_lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        self.connection.settimeout(10)
        try:
            raw = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
            if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                raise DevError("INVALID_REQUEST", "Expected one bounded JSON line.")
            try:
                message = json.loads(raw)
            except ValueError as exc:
                raise DevError("INVALID_REQUEST", "Expected valid JSON.") from exc
            if not isinstance(message, dict):
                raise DevError("INVALID_REQUEST", "Request must be a JSON object.")
            token = message.get("token")
            if not isinstance(token, str) or not secrets.compare_digest(token, self.server.token):
                raise DevError("AUTH_FAILED", "Invalid daemon token.")
            method, params = message.get("method"), message.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise DevError("INVALID_REQUEST", "Expected method string and params object.")
            if method not in {"ping", "daemon.stop"} and (
                message.get("version") != __version__ or message.get("build_id") != _BUILD_ID
            ):
                raise DevError(
                    "DAEMON_VERSION_MISMATCH",
                    "Client and service builds differ; stop the daemon before continuing",
                )
            if method == "ping":
                result = {
                    "pid": os.getpid(),
                    "version": __version__,
                    "build_id": _BUILD_ID,
                    "project": str(self.server.project),
                    "stopping": self.server.stopping.is_set(),
                }
            elif method == "daemon.stop":
                self.server.stopping.set()
                result = {"stopped": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                with self.server.operation_lock:
                    if self.server.stopping.is_set():
                        raise DevError(
                            "DAEMON_STOPPING",
                            "Daemon is shutting down; operation was not dispatched.",
                        )
                    result = self.server.service.dispatch(method, params)
            response = envelope(result)
        except DevError as exc:
            response = envelope(error=exc)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            response = envelope(error=DevError("INTERNAL_ERROR", "See daemon.log for details."))
        try:
            encoded = json.dumps(response, allow_nan=False).encode() + b"\n"
        except (TypeError, ValueError):
            traceback.print_exc(file=sys.stderr)
            encoded = (
                json.dumps(
                    envelope(
                        error=DevError(
                            "INTERNAL_ERROR",
                            "Response serialization failed. The operation may have completed; "
                            "inspect persisted evidence before retrying.",
                        )
                    )
                ).encode()
                + b"\n"
            )
        try:
            if len(encoded) > MAX_RESPONSE_BYTES:
                encoded = (
                    json.dumps(
                        envelope(
                            error=DevError(
                                "RESPONSE_TOO_LARGE",
                                "Response exceeds 16 MiB; inspect persisted artifacts.",
                            )
                        )
                    ).encode()
                    + b"\n"
                )
            self.wfile.write(encoded)
        except OSError:
            traceback.print_exc(file=sys.stderr)


def run_daemon(project: Path, service_factory: Any = None) -> None:
    project = project.resolve()
    directory = project / ".maa-dev"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with project_lock(directory / "daemon.owner.lock", blocking=False):
            if service_factory is None:
                from .service import DevelopmentService

                service_factory = DevelopmentService
            service = service_factory(project)
            token = secrets.token_hex(32)
            server = _Server(service, token, project)
            state = {
                "schema_version": SCHEMA_VERSION,
                "pid": os.getpid(),
                "port": server.server_address[1],
                "token": token,
            }
            _write_state(project, state)
            try:
                server.serve_forever(poll_interval=0.1)
            finally:
                server.server_close()
                service.close()
                if _read_state(project) == state:
                    _state_path(project).unlink(missing_ok=True)
    except BlockingIOError as exc:
        raise DevError("DAEMON_ALREADY_RUNNING", "A daemon already owns this project.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    arguments = parser.parse_args()
    run_daemon(arguments.project)


if __name__ == "__main__":
    main()
