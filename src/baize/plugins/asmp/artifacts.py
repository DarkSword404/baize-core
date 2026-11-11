"""Raw artifact store for evidence.

Evidence rows keep a reference to the raw observation, not just an LLM summary.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, engagement_id: str, artifact_id: str, data: Any) -> str:
        safe_engagement = "".join(c for c in engagement_id if c.isalnum() or c in "-_") or "engagement"
        safe_id = "".join(c for c in artifact_id if c.isalnum() or c in "-_") or "artifact"
        path = self.root / safe_engagement / f"{safe_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return str(path)

    def get(self, ref: str) -> Any:
        path = Path(ref)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
