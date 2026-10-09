"""SQLite checkpoints with generations, units and batches (WAL)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .models import GenerationInfo, TranslationUnit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    source_manifest TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    profile_fingerprint TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    protection_revision TEXT NOT NULL,
    base_generation TEXT,
    model_fingerprint TEXT NOT NULL,
    staging TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS units (
    unit_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    protected_source TEXT NOT NULL,
    status TEXT NOT NULL,
    accepted_target TEXT,
    failure TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Checkpoint:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def new_generation(self, info: GenerationInfo) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                info.generation_id,
                info.mode,
                info.source_manifest,
                info.target_lang,
                info.profile_fingerprint,
                info.policy_revision,
                info.protection_revision,
                info.base_generation,
                info.model_fingerprint,
                info.staging,
                info.status,
                info.created_at,
            ),
        )
        self.conn.commit()

    def latest_compatible(self, info: GenerationInfo) -> GenerationInfo | None:
        row = self.conn.execute(
            "SELECT * FROM generations WHERE mode=? AND source_manifest=? "
            "AND target_lang=? AND profile_fingerprint=? AND policy_revision=? "
            "AND protection_revision=? AND model_fingerprint=? AND status='active' "
            "ORDER BY created_at DESC LIMIT 1",
            (
                info.mode,
                info.source_manifest,
                info.target_lang,
                info.profile_fingerprint,
                info.policy_revision,
                info.protection_revision,
                info.model_fingerprint,
            ),
        ).fetchone()
        if row is None:
            return None
        return GenerationInfo(
            generation_id=row["id"],
            mode=row["mode"],
            source_manifest=row["source_manifest"],
            target_lang=row["target_lang"],
            profile_fingerprint=row["profile_fingerprint"],
            policy_revision=row["policy_revision"],
            protection_revision=row["protection_revision"],
            base_generation=row["base_generation"],
            model_fingerprint=row["model_fingerprint"],
            staging=row["staging"],
            status=row["status"],
            created_at=row["created_at"],
        )

    def compatible_generation_ids(self, info: GenerationInfo) -> list[str]:
        """All generation ids whose compatibility fields match, newest first.

        Compatibility on the manifest is subset-based: a generation whose
        source set is contained in the current one (the corpus grew, e.g.
        a new file format joined discovery) still seeds unit reuse, since
        unit ids are stable per path. Generation status is deliberately
        not a filter: an abandoned generation still holds valid committed
        units that a new generation can seed from.
        """
        rows = self.conn.execute(
            "SELECT id, source_manifest FROM generations WHERE mode=? "
            "AND target_lang=? AND profile_fingerprint=? AND policy_revision=? "
            "AND protection_revision=? AND model_fingerprint=? "
            "ORDER BY created_at DESC, id DESC",
            (
                info.mode,
                info.target_lang,
                info.profile_fingerprint,
                info.policy_revision,
                info.protection_revision,
                info.model_fingerprint,
            ),
        ).fetchall()
        new_paths = {p for p in (info.source_manifest or "").split(",") if p}
        out: list[str] = []
        for row in rows:
            old_paths = {p for p in (row["source_manifest"] or "").split(",") if p}
            if old_paths and old_paths <= new_paths:
                out.append(row["id"])
        return out

    def accepted_units_merged(self, generation_ids: list[str]) -> dict[str, str]:
        """Accepted targets across generations, newest generation wins per unit."""
        merged: dict[str, str] = {}
        for generation_id in reversed(generation_ids):
            rows = self.conn.execute(
                "SELECT unit_id, accepted_target FROM units "
                "WHERE generation_id=? AND status='accepted'",
                (generation_id,),
            ).fetchall()
            for r in rows:
                merged[r["unit_id"]] = r["accepted_target"]
        return merged

    def accepted_units(self, generation_id: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT unit_id, accepted_target FROM units "
            "WHERE generation_id=? AND status='accepted'",
            (generation_id,),
        ).fetchall()
        return {r["unit_id"]: r["accepted_target"] for r in rows}

    def get_unit(self, unit_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM units WHERE unit_id=?", (unit_id,)).fetchone()

    def save_units(self, units: list[TranslationUnit], generation_id: str) -> None:
        for u in units:
            self.conn.execute(
                "INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?,?)",
                (
                    u.unit_id,
                    generation_id,
                    u.source_hash,
                    u.protected_source,
                    u.status.value,
                    u.accepted_target,
                    None,
                    now_iso(),
                ),
            )
        self.conn.commit()

    def record_batch(self, batch_id: str, generation_id: str, status: str, error: str | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO batches VALUES (?,?,?,?,?,?,?)",
            (
                batch_id,
                generation_id,
                status,
                0,
                error,
                now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()

    def mark_generation(self, generation_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE generations SET status=? WHERE id=?", (status, generation_id)
        )
        self.conn.commit()

    @staticmethod
    def manifest_of(files: list[str]) -> str:
        return json.dumps(sorted(files), ensure_ascii=False)