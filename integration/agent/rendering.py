"""Reproduce a changed real browser rendering environment without changing app bytes."""

from __future__ import annotations

import contextlib
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Iterator

CONFIG = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include>/etc/fonts/fonts.conf</include>
  <reset-dirs/>
  <dir>/usr/share/fonts/truetype/liberation</dir>
  <dir>/usr/share/fonts/truetype/liberation2</dir>
  <match target="pattern">
    <edit name="family" mode="assign_replace" binding="strong"><string>Liberation Sans</string></edit>
  </match>
</fontconfig>
"""


@contextlib.contextmanager
def fontconfig_environment(path: str | None) -> Iterator[None]:
    previous = os.environ.get("FONTCONFIG_FILE")
    if path is None:
        os.environ.pop("FONTCONFIG_FILE", None)
    else:
        os.environ["FONTCONFIG_FILE"] = path
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("FONTCONFIG_FILE", None)
        else:
            os.environ["FONTCONFIG_FILE"] = previous


@contextlib.contextmanager
def changed_rendering(private: Path) -> Iterator[dict]:
    config = private / "browser-runtime.conf"
    config.write_text(CONFIG, encoding="utf-8")
    with fontconfig_environment(str(config)):
        selected = subprocess.run(
            ["fc-match", "-f", "%{family}", "system-ui"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        if selected != "Liberation Sans":
            raise ValueError("Install fonts-liberation before the changed-rendering preflight")
        yield {
            "kind": "font_fallback_environment_change",
            "selected_family": selected,
            "configuration_sha256": hashlib.sha256(CONFIG.encode()).hexdigest(),
            "upstream_application_bytes_modified": False,
        }
