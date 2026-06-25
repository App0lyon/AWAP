"""Artifact storage for large workflow payload fragments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

ARTIFACT_REFERENCE_KEY = "awap_artifact_ref"


class LocalArtifactStore:
    """Stores JSON artifacts on the local filesystem.

    The interface is intentionally small so a production object-store backend can
    replace it without changing the payload-shaping code.
    """

    backend_name = "filesystem"

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    def write_json(self, *, run_id: str, label: str, value: Any) -> dict[str, Any]:
        raw = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
        artifact_id = str(uuid4())
        run_dir = self._root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"{artifact_id}.json"
        path.write_bytes(raw)
        return {
            ARTIFACT_REFERENCE_KEY: True,
            "artifact_id": artifact_id,
            "backend": self.backend_name,
            "uri": str(path),
            "media_type": "application/json",
            "size_bytes": len(raw),
            "label": label,
        }

    def read_json(self, reference: dict[str, Any]) -> Any:
        if reference.get(ARTIFACT_REFERENCE_KEY) is not True:
            return reference
        if reference.get("backend") != self.backend_name:
            return reference
        uri = reference.get("uri")
        if not isinstance(uri, str):
            return reference
        path = Path(uri).expanduser().resolve()
        if path != self._root and self._root not in path.parents:
            raise RuntimeError("Artifact reference points outside the configured artifact root.")
        return json.loads(path.read_text(encoding="utf-8"))
