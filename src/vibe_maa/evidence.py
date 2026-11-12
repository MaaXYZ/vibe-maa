import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .errors import DevError


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_image(path: Path, image: np.ndarray) -> str:
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise DevError("INVALID_FRAME", "Expected a BGR uint8 image with three channels")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image[:, :, ::-1]).save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_image(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))[:, :, ::-1].copy()


class EvidenceStore:
    def __init__(self, project: Path):
        self.root = project.resolve() / ".maa-dev"
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, kind: str, artifact_id: str) -> Path:
        if kind not in {"observations", "actions", "runs", "probes"}:
            raise DevError("INVALID_ARGUMENT", "Unknown artifact kind")
        if not artifact_id or any(
            c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in artifact_id
        ):
            raise DevError("INVALID_ARGUMENT", "Invalid artifact ID")
        return self.root / kind / artifact_id

    def read(self, kind: str, artifact_id: str) -> dict[str, Any]:
        path = self.directory(kind, artifact_id) / "record.json"
        if not path.is_file():
            raise DevError("NOT_FOUND", "Artifact not found", {"id": artifact_id})
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, kind: str, artifact_id: str, record: dict[str, Any]) -> None:
        write_json(self.directory(kind, artifact_id) / "record.json", record)
