from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TW = Path(__file__).resolve().parent.parent
if str(TW) not in sys.path:
    sys.path.insert(0, str(TW))

from native import Invalid, sha256_bytes
import storage
from storage import Store, digest, durable, json_bytes, safe
from toolkit import DEFAULT_BATCH_SIZE, Project, attach_fingerprints, lines_bytes


def _build_scale_workspace(root: Path, *, num_batches: int = 105, per_batch: int = 5) -> list[dict]:
    trans = root / "translation-work" / "translations"
    trans.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    batches: list[str] = []
    for bi in range(1, num_batches + 1):
        bname = f"batch-{bi:04d}.jsonl"
        batches.append(bname)
        b_rows: list[dict] = []
        for si in range(per_batch):
            row = {
                "id": f"scale:{bi}:{si}",
                "row": len(all_rows),
                "source_file": f"The Iron Oath_Data/StreamingAssets/Data/Scale/f{bi}.json",
                "format": "hjson",
                "key": f"{si}:Text",
                "context": {"entry_id": f"e_{bi}_{si}"},
                "EN": f"Line {bi}-{si}",
                "PTBR": "",
            }
            attach_fingerprints([row])
            b_rows.append(row)
            all_rows.append(row)
        (trans / bname).write_bytes(lines_bytes(b_rows))
    manifest = {
        "schema": 1,
        "game": "The Iron Oath",
        "native_version": "1.0.021",
        "generation": "scale-gen-001",
        "batch_size": DEFAULT_BATCH_SIZE,
        "entries": len(all_rows),
        "batches": batches,
        "counts": {"hjson": len(all_rows)},
        "sources": {},
    }
    (trans / "manifest.json").write_bytes(json_bytes(manifest))
    return all_rows


class StorageAndScaleTests(unittest.TestCase):
    def test_105_batches_500_consecutive_tiny_applies_dirty_only_and_bounded_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            all_rows = _build_scale_workspace(root, num_batches=105, per_batch=5)
            proj = Project(root)
            trans_dir = root / "translation-work" / "translations"

            # Record initial mtimes and hashes across all 105 batches
            initial_stats = {
                p.name: (p.stat().st_mtime_ns, p.read_bytes())
                for p in sorted(trans_dir.glob("batch-*.jsonl"))
            }
            self.assertEqual(len(initial_stats), 105)

            payload_path = root / "payload.json"
            # Apply 500 consecutive single-entry updates touching only the first 100 batches (leaving batches 101..105 untouched!)
            with mock.patch("storage.os.fsync"):
                for idx in range(500):
                    rec = all_rows[idx]
                    payload_path.write_bytes(
                        json_bytes(
                            {
                                "generation": "scale-gen-001",
                                "entries": [
                                    {
                                        "id": rec["id"],
                                        "source_fingerprint": rec["source_fingerprint"],
                                        "PTBR": f"Linha {idx}",
                                    }
                                ],
                            }
                        )
                    )
                    res = proj.apply(payload_path)
                    self.assertEqual(res["status"], "ok")
                    self.assertEqual(len(res["touched_batches"]), 1)

            # Untouched batches 0101..0105 must never have been rewritten
            for bi in range(101, 106):
                bname = f"batch-{bi:04d}.jsonl"
                p = trans_dir / bname
                self.assertEqual(p.read_bytes(), initial_stats[bname][1])
                self.assertEqual(p.stat().st_mtime_ns, initial_stats[bname][0])

            # Bounded snapshot count & bytes under 500 sequential applies
            st = proj.store.stats()
            self.assertLessEqual(st["completed"], 10)
            self.assertLessEqual(st["bytes"], 512 * 1024 * 1024)
            self.assertEqual(st["protected"], 0)

    def test_crash_recover_rollback_and_gc_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "translation-work").mkdir(parents=True, exist_ok=True)
            (root / "data.txt").write_bytes(b"v0\n")
            store = Store(root, count=2, budget=1024 * 1024, headroom=4 * 1024)

            tx1 = store.commit({"data.txt": b"v1\n"}, expected={"data.txt": sha256_bytes(b"v0\n")}, operation="apply")
            tx2 = store.commit({"data.txt": b"v2\n"}, expected={"data.txt": sha256_bytes(b"v1\n")}, operation="apply")
            self.assertIsNotNone(tx1)
            self.assertIsNotNone(tx2)
            self.assertEqual((root / "data.txt").read_bytes(), b"v2\n")

            # Simulated promotion crash leaves ready transaction without 'complete'
            (root / "extra.txt").write_bytes(b"before\n")
            with self.assertRaises(Invalid) as ctx:
                store.commit(
                    {"data.txt": b"v3\n", "extra.txt": b"after\n"},
                    expected={"data.txt": sha256_bytes(b"v2\n"), "extra.txt": sha256_bytes(b"before\n")},
                    operation="apply",
                    crash_after=1,
                )
            self.assertIn("SIMULATED_CRASH", str(ctx.exception))

            # GC must refuse to delete anything while interrupted transaction awaits recover
            with self.assertRaises(Invalid) as gc_err:
                store.gc(dry=True)
            self.assertIn("RECOVERY_REQUIRED", str(gc_err.exception))

            recovered = store.recover()
            self.assertEqual(len(recovered), 1)
            self.assertEqual((root / "data.txt").read_bytes(), b"v2\n")
            self.assertEqual((root / "extra.txt").read_bytes(), b"before\n")

            # Rollback only allowed on latest verified completed snapshot, not recovered or stale
            with self.assertRaises(Invalid) as rb_err:
                store.rollback(snapshot_id=tx1)
            self.assertIn("ROLLBACK_DENIED", str(rb_err.exception))

            # Fresh verified commit -> rollback restores previous byte content
            tx_live = store.commit({"data.txt": b"v3\n"}, expected={"data.txt": sha256_bytes(b"v2\n")}, operation="apply")
            store.rollback(snapshot_id=tx_live)
            self.assertEqual((root / "data.txt").read_bytes(), b"v2\n")

            # Lock contention blocks new lock acquisition until released
            with store.lock():
                with self.assertRaises(Invalid) as lk_err:
                    with store.lock():
                        pass
                self.assertIn("LOCKED", str(lk_err.exception))

            # Corrupt receipt blocks GC
            tx_folders = sorted(store.txns.iterdir())
            newest_sha = tx_folders[-1] / "receipt.sha256"
            saved_sha = newest_sha.read_bytes()
            newest_sha.write_bytes(b"0" * 64 + b"\n")
            with self.assertRaises(Invalid) as cor_err:
                store.gc(dry=True)
            self.assertIn("CORRUPT_RECEIPT", str(cor_err.exception))
            newest_sha.write_bytes(saved_sha)

            # Insufficient headroom blocks GC
            FakeUsage = type("Usage", (), {"total": 1000, "used": 999, "free": 100})
            with mock.patch("storage.shutil.disk_usage", return_value=FakeUsage()):
                with self.assertRaises(Invalid) as sp_err:
                    store.gc(dry=True)
                self.assertIn("INSUFFICIENT_SPACE", str(sp_err.exception))

            # gc --dry-run preserves sole newest snapshot and never mutates completed count
            before_count = len(list(store.txns.iterdir()))
            dry_plan = store.gc(dry=True)
            self.assertEqual(len(list(store.txns.iterdir())), before_count)
            store.gc(dry=False)
            self.assertGreaterEqual(len(list(store.txns.iterdir())), 1)

            # Path traversal / escape outside workspace root is always blocked
            with self.assertRaises(Invalid):
                safe(root, "../outside.txt")

    def test_gc_refuses_symlinks_and_sole_snapshot_over_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "translation-work").mkdir(parents=True, exist_ok=True)
            (root / "big.bin").write_bytes(b"x" * 1024)
            store = Store(root, count=10, budget=65536, headroom=0)
            store.commit({"big.bin": b"y" * 2048}, operation="apply")

            # Shrink budget below sole snapshot size -> must refuse deleting sole snapshot
            tight_store = Store(root, count=10, budget=1024, headroom=0)
            with self.assertRaises(Invalid) as err:
                tight_store.gc(dry=True)
            self.assertIn("RETENTION_BUDGET", str(err.exception))

            # Symlink test (best-effort on OS/privilege)
            link_path = root / "translation-work" / "escape"
            try:
                os.symlink(tmp, link_path)
            except OSError:
                return
            with self.assertRaises(Invalid):
                safe(root, "translation-work/escape")

    def test_durable_retries_transient_permission_error_and_cleans_temp_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "bundle.bin"
            real_replace = os.replace
            attempts = {"count": 0}

            def flaky_replace(src: Path, dst: Path) -> None:
                attempts["count"] += 1
                if attempts["count"] <= 2:
                    raise PermissionError("transient lock")
                real_replace(src, dst)

            with (
                mock.patch.object(storage.os, "name", "nt"),
                mock.patch.object(storage.os, "replace", side_effect=flaky_replace),
                mock.patch.object(storage.time, "sleep") as sleep_mock,
            ):
                durable(target, b"patched")
            self.assertEqual(target.read_bytes(), b"patched")
            self.assertEqual(attempts["count"], 3)
            self.assertEqual(sleep_mock.call_count, 2)
            self.assertEqual([p.name for p in root.iterdir()], ["bundle.bin"])

            with (
                mock.patch.object(storage.os, "name", "nt"),
                mock.patch.object(storage.os, "replace", side_effect=PermissionError("locked")),
                mock.patch.object(storage.time, "sleep"),
            ):
                with self.assertRaises(PermissionError):
                    durable(target, b"failed")
            self.assertEqual([p.name for p in root.iterdir()], ["bundle.bin"])
            self.assertEqual(target.read_bytes(), b"patched")


if __name__ == "__main__":
    unittest.main()
