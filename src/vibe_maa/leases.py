import getpass
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .daemon import project_lock
from .errors import DevError


def acquire_device(device: str) -> Any:
    """Hold an OS lease across project daemons using the same ADB endpoint."""
    identity = device.strip().lower().replace("localhost:", "127.0.0.1:")
    user = str(os.getuid()) if hasattr(os, "getuid") else getpass.getuser()
    user_key = hashlib.sha256(user.encode()).hexdigest()[:16]
    directory = Path(tempfile.gettempdir()) / f"vibe-maa-{user_key}"
    directory.mkdir(mode=0o700, exist_ok=True)
    key = hashlib.sha256(identity.encode()).hexdigest()
    lease = project_lock(directory / f"adb-{key}.lock", blocking=False)
    try:
        lease.__enter__()
    except OSError as exc:
        raise DevError("DEVICE_BUSY", "Another session owns this ADB endpoint") from exc
    return lease
