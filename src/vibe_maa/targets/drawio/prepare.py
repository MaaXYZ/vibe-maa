"""Provision an immutable upstream draw.io release for local-only integration tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vibe_maa.targets.archive import ProvisionError as ProvisionError
from vibe_maa.targets.archive import provision_archive

MANIFEST_PATH = Path(__file__).with_name("manifest.json")


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def provision(cache: Path, archive: Path | None = None) -> Path:
    """Return the verified draw.io webroot, optionally using a downloaded release."""
    return provision_archive(
        cache,
        target="drawio",
        metadata=manifest(),
        archive_suffix=".war",
        required_files=("index.html", "js/app.min.js", "js/bootstrap.js", "styles/grapheditor.css"),
        archive=archive,
        excluded_prefixes=("WEB-INF", "META-INF"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("work/targets"))
    parser.add_argument("--archive", type=Path)
    arguments = parser.parse_args()
    webroot = provision(arguments.cache, arguments.archive)
    print(json.dumps({"webroot": str(webroot), "target": manifest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
