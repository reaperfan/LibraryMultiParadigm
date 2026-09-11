"""Tartós futásállapot és zárolás helyi állományokban (a cserélendő adatbázistól függetlenül).

``state/current_run.json``  – az éppen futó vagy félbeszakadt csere állapota
``state/active_instance.json`` – az utoljára sikeresen átállított aktív példány
``state/rotation.lock``     – egyszerre csak egy csere futhat
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RunState:
    run_id: str
    started_at: str
    reason: str
    source_postgres_id: str
    approved_source_id: str
    allow_delete_source_id: str | None
    maintenance_at: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    backup_path: str | None = None
    new_postgres_id: str | None = None
    new_postgres_name: str | None = None
    deploy_id: str | None = None
    status: str = "running"  # running | failed | done
    last_error: str | None = None
    updated_at: str | None = None

    def mark_step(self, step: str) -> None:
        if step not in self.completed_steps:
            self.completed_steps.append(step)


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.run_file = state_dir / "current_run.json"
        self.active_file = state_dir / "active_instance.json"
        self.lock_file = state_dir / "rotation.lock"

    # ----- futásállapot -----
    def save_run(self, state: RunState) -> None:
        state.updated_at = datetime.now(UTC).isoformat()
        tmp = self.run_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.run_file)

    def load_run(self) -> RunState | None:
        if not self.run_file.exists():
            return None
        return RunState(**json.loads(self.run_file.read_text(encoding="utf-8")))

    def archive_run(self, state: RunState) -> None:
        archive = self.state_dir / "history"
        archive.mkdir(exist_ok=True)
        (archive / f"run_{state.run_id}.json").write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self.run_file.exists():
            self.run_file.unlink()

    # ----- aktív példány -----
    def save_active(self, postgres_id: str, run_id: str, maintenance_at: str | None) -> None:
        payload = {
            "postgres_id": postgres_id,
            "run_id": run_id,
            "switched_at": datetime.now(UTC).isoformat(),
            "maintenance_at": maintenance_at,
        }
        self.active_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_active(self) -> dict | None:
        if not self.active_file.exists():
            return None
        return json.loads(self.active_file.read_text(encoding="utf-8"))

    # ----- zárolás -----
    def acquire_lock(self, run_id: str) -> bool:
        try:
            fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            logger.error("Már fut egy csere (zárfájl: %s)", self.lock_file)
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(run_id)
        return True

    def release_lock(self) -> None:
        if self.lock_file.exists():
            self.lock_file.unlink()
