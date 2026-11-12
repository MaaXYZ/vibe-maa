"""Provision an immutable upstream draw.io release for local-only integration tests.

This script intentionally depends only on Python's standard library. No app
source or browser binary is committed to vibe-maa. Runtime serving is separate
and must reject requests outside the fixture's loopback origin.
"""

from __future__ import annotations

import argparse
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

MANIFEST_PATH = Path(__file__).with_name("manifest.json")
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_FILES = 10000


class ProvisionError(RuntimeError):
    pass


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


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


def _extract(archive: Path, output: Path) -> dict[str, str]:
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
            if not name.parts or name.parts[0] in {"WEB-INF", "META-INF"} or entry.is_dir():
                continue
            destination = output.joinpath(*name.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
            files[name.as_posix()] = _digest(destination)
    for required in ("index.html", "js/app.min.js", "js/bootstrap.js", "styles/grapheditor.css"):
        if required not in files:
            raise ProvisionError(
                f"Upstream release is missing required application file: {required}"
            )
    return files


def _verified_webroot(webroot: Path, metadata: dict[str, Any]) -> bool:
    receipt = webroot.parent / "receipt.json"
    try:
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        if saved["archive_sha256"] != metadata["archive_sha256"]:
            return False
        files = saved["files"]
        if not isinstance(files, dict) or not files:
            return False
        actual = {p.relative_to(webroot).as_posix() for p in webroot.rglob("*") if p.is_file()}
        if actual != set(files):
            return False
        return all(_digest(webroot / name) == digest for name, digest in files.items())
    except (OSError, ValueError, KeyError):
        return False


def provision(cache: Path, archive: Path | None = None) -> Path:
    """Return a verified, extracted webroot; download only during this explicit step.

    ``archive`` allows a pre-downloaded release to be used in disconnected CI. It
    is still checked against the exact upstream size and SHA256.
    """
    metadata = manifest()
    cache = Path(cache).resolve()
    release = cache / f"drawio-{metadata['release']}-{metadata['archive_sha256'][:12]}"
    cache.mkdir(parents=True, exist_ok=True)
    with _lock(cache / "drawio-provision.lock"):
        webroot = release / "webroot"
        if _verified_webroot(webroot, metadata):
            return webroot
        pinned = cache / f"drawio-{metadata['release']}.war"
        if archive is not None:
            archive = Path(archive).resolve()
            if not _verified_archive(archive, metadata):
                raise ProvisionError("Provided archive does not match the pinned draw.io release")
            if archive != pinned:
                shutil.copyfile(archive, pinned)
        elif not _verified_archive(pinned, metadata):
            _download(pinned, metadata)
        temporary = Path(tempfile.mkdtemp(prefix="drawio-extract-", dir=cache))
        try:
            files = _extract(pinned, temporary / "webroot")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("work/targets"))
    parser.add_argument("--archive", type=Path)
    arguments = parser.parse_args()
    webroot = provision(arguments.cache, arguments.archive)
    print(json.dumps({"webroot": str(webroot), "target": manifest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
