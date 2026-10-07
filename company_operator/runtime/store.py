"""Run storage: one directory per run, with an atomically replaced checkpoint.

data/runs/<run_id>/
    state.json      latest checkpoint (written after every transition)
    events.jsonl    append-only audit log
    evidence/       screenshots and downloaded files
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from company_operator.runtime.models import RunState, TaskRequest, now


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def create(self, request: TaskRequest) -> RunState:
        run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        self.run_dir(run_id).mkdir(parents=True)
        state = RunState(run_id=run_id, request=request)
        self.save(state)
        return state

    def save(self, state: RunState) -> None:
        state.updated_at = now()
        path = self.run_dir(state.run_id) / "state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash leaves either the old or the new checkpoint, never half

    def load(self, run_id: str) -> RunState:
        path = self.run_dir(run_id) / "state.json"
        return RunState.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[str]:
        return sorted((p.name for p in self.root.iterdir() if (p / "state.json").exists()), reverse=True)
