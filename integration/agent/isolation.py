"""Expose only the tool runtime and writable submission from a frozen checkout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "vibe_agent"


def filesystem_policy(
    project: Path, root: Path = ROOT, protected: tuple[Path, ...] = ()
) -> dict[str, str]:
    root, project = root.resolve(), project.resolve()
    readable = [root / "src", root / ".venv", root / "integration/agent/node_modules"]
    allowed = [*readable, project]
    rules = {"/": "read", "/tmp": "write"}

    def partition(directory: Path) -> None:
        for child in sorted(directory.iterdir()):
            path = child.resolve()
            if path in allowed:
                continue
            if (
                any(keep.is_relative_to(path) for keep in allowed)
                and child.is_dir()
                and not child.is_symlink()
            ):
                partition(child)
            else:
                rules[str(child)] = "deny"

    # A denied parent cannot reliably be re-exposed by a child rule in the pinned Linux backend.
    # Deny sibling branches instead, leaving only the runtime/project ancestor chain traversable.
    partition(root)
    for path in readable:
        if path.is_dir():
            rules[str(path)] = "read"
    rules[str(project)] = "write"
    # The packaged mailbox answer is host initialization data, not an agent reference.
    mailbox = root / "src/vibe_maa/data/mailbox"
    if mailbox.is_dir():
        rules[str(mailbox)] = "deny"
    for path in (project.parent / "private", project.parent / "published", *protected):
        path = path.resolve()
        if path == project or project.is_relative_to(path):
            raise ValueError("A protected path cannot contain the writable submission")
        if path.exists():
            rules[str(path)] = "deny"
    return rules


def profile_arguments(
    project: Path, root: Path = ROOT, *, select: bool = True, protected: tuple[Path, ...] = ()
) -> list[str]:
    rules = filesystem_policy(project, root, protected)
    filesystem = (
        "{"
        + ",".join(f"{json.dumps(path)}={json.dumps(mode)}" for path, mode in rules.items())
        + "}"
    )
    arguments = [
        "-c",
        f"permissions.{PROFILE}.filesystem={filesystem}",
        "-c",
        f"permissions.{PROFILE}.network.enabled=true",
    ]
    if select:
        arguments[:0] = ["-c", f"default_permissions={json.dumps(PROFILE)}"]
    return arguments


def policy_metadata(project: Path) -> dict[str, str]:
    encoded = json.dumps(filesystem_policy(project), sort_keys=True).encode()
    return {
        "scope": "checkout_runtime_allowlist",
        "profile": PROFILE,
        "rules_sha256": hashlib.sha256(encoded).hexdigest(),
        "network_egress": "not_isolated_by_this_filesystem_policy",
    }
