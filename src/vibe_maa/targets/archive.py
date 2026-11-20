"""Verify and cache pinned ZIP or WAR releases for local application integration tests."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_FILES = 10000


class ProvisionError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            if not stream.read(1):
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _verified_archive(path: Path, metadata: dict[str, Any]) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == metadata["archive_size"]
        and (_digest(path) == metadata["archive_sha256"])
    )


def _download(destination: Path, metadata: dict[str, Any]) -> None:
    temporary = destination.with_name(f"download-{uuid.uuid4().hex}.tmp")
    request = urllib.request.Request(
        metadata["archive_url"], headers={"User-Agent": "vibe-maa-test-fixture/1"}
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            temporary.open("xb") as stream,
        ):
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ProvisionError("Upstream archive exceeds the download budget")
                stream.write(chunk)
        if not _verified_archive(temporary, metadata):
            raise ProvisionError(
                "Upstream archive size or SHA256 does not match the pinned release"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _extract(
    archive: Path,
    output: Path,
    required_files: tuple[str, ...],
    excluded_prefixes: tuple[str, ...],
) -> dict[str, str]:
    files: dict[str, str] = {}
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if (
            len(entries) > MAX_FILES
            or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES
        ):
            raise ProvisionError("Upstream archive exceeds the extraction budget")
        for entry in entries:
            name = PurePosixPath(entry.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in entry.filename
                or any(":" in part for part in name.parts)
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ProvisionError(f"Unsafe path in upstream archive: {entry.filename}")
            if not name.parts or name.parts[0] in excluded_prefixes or entry.is_dir():
                continue
            destination = output.joinpath(*name.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
            files[name.as_posix()] = _digest(destination)
    for required in required_files:
        if required not in files:
            raise ProvisionError(
                f"Upstream release is missing required application file: {required}"
            )
    return files


def _verified_webroot(
    webroot: Path,
    metadata: dict[str, Any],
    required_files: tuple[str, ...],
    excluded_prefixes: tuple[str, ...],
) -> bool:
    receipt = webroot.parent / "receipt.json"
    try:
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or saved.get("archive_sha256") != metadata["archive_sha256"]:
            return False
        files = saved.get("files")
        if not isinstance(files, dict) or not files or not set(required_files).issubset(files):
            return False
        if webroot.is_symlink():
            return False
        actual = {}
        for path in webroot.rglob("*"):
            relative = path.relative_to(webroot)
            if path.is_symlink() or relative.parts[0] in excluded_prefixes:
                return False
            if path.is_file():
                actual[relative.as_posix()] = path
        if set(actual) != set(files):
            return False
        return all(_digest(actual[name]) == digest for name, digest in files.items())
    except (OSError, ValueError):
        return False


def provision_archive(
    cache: Path,
    *,
    target: str,
    metadata: dict[str, Any],
    archive_suffix: str,
    required_files: tuple[str, ...],
    archive: Path | None = None,
    excluded_prefixes: tuple[str, ...] = (),
) -> Path:
    """Return a verified, extracted webroot; download only during this explicit step.

    ``archive`` allows a pre-downloaded release to be used in disconnected CI. It
    is still checked against the exact upstream size and SHA256.
    """
    cache = Path(cache).resolve()
    release_name = f"{target}-{metadata['release']}"
    release = cache / f"{release_name}-{metadata['archive_sha256'][:12]}"
    cache.mkdir(parents=True, exist_ok=True)
    with _lock(cache / f"{target}-provision.lock"):
        if archive is not None:
            archive = Path(archive).resolve()
            if not _verified_archive(archive, metadata):
                raise ProvisionError(f"Provided archive does not match the pinned {target} release")
        webroot = release / "webroot"
        if _verified_webroot(webroot, metadata, required_files, excluded_prefixes):
            return webroot
        pinned = cache / f"{release_name}{archive_suffix}"
        if archive is not None:
            if archive != pinned:
                shutil.copyfile(archive, pinned)
        elif not _verified_archive(pinned, metadata):
            _download(pinned, metadata)
        temporary = Path(tempfile.mkdtemp(prefix=f"{target}-extract-", dir=cache))
        try:
            files = _extract(pinned, temporary / "webroot", required_files, excluded_prefixes)
            (temporary / "receipt.json").write_text(
                json.dumps({**metadata, "files": files}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if release.exists():
                shutil.rmtree(release)
            os.replace(temporary, release)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return release / "webroot"
