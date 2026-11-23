"""Bounded copy-on-write transactions, crash recovery, and safe pruning."""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import uuid

from native import Invalid

_DEFER_FSYNC = contextvars.ContextVar("_DEFER_FSYNC", default=False)
_BULK_THRESHOLD = 32


@contextlib.contextmanager
def _bulk_write(enabled: bool):
    if not enabled:
        yield
        return
    token = _DEFER_FSYNC.set(True)
    try:
        yield
    finally:
        _DEFER_FSYNC.reset(token)


def _report_progress(operation: str, phase: str, done: int, total: int, last_tick: float) -> float:
    if total <= 50:
        return last_tick
    now = time.monotonic()
    if done == total or done % 1000 == 0 or (now - last_tick) >= 2.0:
        print(f"[{operation}] {phase}: {done}/{total}", file=sys.stderr, flush=True)
        return now
    return last_tick


def _prune_empty_parents(path: Path, stop_root: Path) -> None:
    parent = path.parent
    while parent != stop_root and parent.is_relative_to(stop_root):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def json_bytes(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def safe(root: Path, rel: str) -> Path:
    """Resolve rel safely inside root, rejecting traversal, absolute paths, and symlinks."""
    p = PurePosixPath(rel)
    if (
        not isinstance(rel, str)
        or not rel
        or "\\" in rel
        or ":" in rel
        or p.is_absolute()
        or any(x in {".", "..", ""} for x in rel.split("/"))
    ):
        raise Invalid(f"UNSAFE_PATH: {rel}")
    root_resolved = Path(root).resolve()
    path = root_resolved
    for part in p.parts:
        path = path / part
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & 0x400):
            raise Invalid(f"UNSAFE_PATH: symlink/junction {path}")
    if not path.is_relative_to(root_resolved):
        raise Invalid(f"UNSAFE_PATH: escapes repository {rel}")
    return path


def digest(path: Path) -> str | None:
    return sha(path.read_bytes()) if path.is_file() else None


def durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    defer = _DEFER_FSYNC.get()
    with tmp.open("xb") as f:
        f.write(data)
        f.flush()
        if not defer:
            os.fsync(f.fileno())
    os.replace(tmp, path)
    if os.name != "nt" and not defer:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class Store:
    def __init__(
        self,
        root: Path,
        count: int = 10,
        budget: int = 512 * 1024 * 1024,
        headroom: int = 4 * 1024 * 1024,
    ):
        self.root = Path(root).resolve()
        self.state = safe(self.root, "translation-work/.state")
        self.txns = safe(self.root, "translation-work/.state/transactions")
        self.count = count
        self.budget = budget
        self.headroom = headroom
        if count < 1 or budget < 1024:
            raise Invalid("Retention must allow at least one transaction and 1024 bytes")

    @contextlib.contextmanager
    def lock(self):
        self.state.mkdir(parents=True, exist_ok=True)
        path = safe(self.root, "translation-work/.state/operation.lock")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise Invalid(
                "LOCKED: another operation or interrupted process; inspect status and run recover --break-lock"
            )
        token = uuid.uuid4().hex
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "token": token}, f)
                f.flush()
                os.fsync(f.fileno())
            yield
        finally:
            if path.exists():
                try:
                    if read_json(path).get("token") == token:
                        path.unlink()
                except Exception:
                    pass

    def break_lock(self) -> None:
        path = self.state / "operation.lock"
        if not path.exists():
            return
        pid = read_json(path)["pid"]
        if os.name == "nt":
            import ctypes

            k = ctypes.windll.kernel32
            k.OpenProcess.restype = ctypes.c_void_p
            k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = k.OpenProcess(0x1000, False, pid)
            active = False
            if handle:
                code = ctypes.c_ulong()
                active = not k.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
                k.CloseHandle(handle)
            elif k.GetLastError() != 87:
                raise Invalid("Cannot establish that lock owner has exited")
        else:
            try:
                os.kill(pid, 0)
                active = True
            except ProcessLookupError:
                active = False
            except PermissionError:
                active = True
        if active:
            raise Invalid(f"LOCKED: owner PID {pid} is still alive")
        path.unlink()

    def receipts(self) -> list[tuple[Path, dict, int, bool]]:
        result: list[tuple[Path, dict, int, bool]] = []
        if not self.txns.exists():
            return result
        for folder in sorted(self.txns.iterdir()):
            safe(self.root, folder.relative_to(self.root).as_posix())
            if not folder.is_dir():
                raise Invalid(f"Unexpected transaction file {folder}")
            if not (folder / "receipt.json").exists():
                raise Invalid(f"INCOMPLETE_STAGING: inspect {folder}; no live promotion was authorized")
            data = (folder / "receipt.json").read_bytes()
            sha_file = folder / "receipt.sha256"
            if not sha_file.is_file() or sha_file.read_text(encoding="utf-8").strip() != sha(data):
                raise Invalid(f"CORRUPT_RECEIPT: {folder.name}")
            receipt = json.loads(data)
            if receipt.get("id") != folder.name:
                raise Invalid(f"Receipt ownership mismatch: {folder.name}")
            for item in receipt.get("files", []):
                safe(self.root, item["path"])
                if item["before"] is not None:
                    backup = folder / item["slot"]
                    safe(self.root, backup.relative_to(self.root).as_posix())
                    staged = (folder / "ready").exists() or (folder / "complete").exists()
                    if (staged or backup.exists()) and digest(backup) != item["before"]:
                        raise Invalid(f"CORRUPT_BACKUP: {folder.name}/{item['slot']}")
            for base, dirs, files in os.walk(folder, followlinks=False):
                for name in dirs + files:
                    safe(self.root, (Path(base) / name).relative_to(self.root).as_posix())
            size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
            complete = (folder / "complete").is_file()
            result.append((folder, receipt, size, complete))
        return result

    def clean_state(self) -> None:
        if any(not complete for _, _, _, complete in self.receipts()):
            raise Invalid("RECOVERY_REQUIRED: interrupted transaction detected; run recover first")

    def stats(self) -> dict:
        rs = self.receipts()
        completed = sum(1 for v in rs if v[3])
        reclaim = 0
        if completed == len(rs):
            try:
                reclaim = sum(v[2] for v in self.gc_plan(rs))
            except Invalid:
                reclaim = 0
        return {
            "transactions": len(rs),
            "completed": completed,
            "protected": len(rs) - completed,
            "bytes": sum(v[2] for v in rs),
            "retention_count": self.count,
            "retention_bytes": self.budget,
            "reclaimable_bytes": reclaim,
        }

    def gc_plan(self, rs=None) -> list[tuple[Path, dict, int, bool]]:
        rs = self.receipts() if rs is None else rs
        if any(not v[3] for v in rs):
            raise Invalid("RECOVERY_REQUIRED: gc preserves all snapshots until recovery")
        if self.txns.exists() and shutil.disk_usage(self.root).free < self.headroom:
            raise Invalid("INSUFFICIENT_SPACE: free space below configured headroom; aborting gc")
        total = sum(v[2] for v in rs)
        number = len(rs)
        selected: list[tuple[Path, dict, int, bool]] = []
        for item in rs[:-1]:  # Always preserve the newest completed snapshot as sole recoverable state
            if number <= self.count and total <= self.budget:
                break
            selected.append(item)
            total -= item[2]
            number -= 1
        if number and total > self.budget:
            raise Invalid("RETENTION_BUDGET: sole recoverable snapshot exceeds configured bytes")
        return selected

    def gc(self, dry: bool = False) -> list[dict]:
        selected = self.gc_plan()
        result = [
            {
                "id": p.name,
                "bytes": n,
                "status": "completed",
                "age_seconds": max(0, int(time.time() - r["created"])),
            }
            for p, r, n, _ in selected
        ]
        if not dry:
            for p, _, _, _ in selected:
                self.receipts()
                shutil.rmtree(p)
        return result

    @staticmethod
    def new_id() -> str:
        return f"{time.time_ns():020d}-{uuid.uuid4().hex[:12]}"

    def commit(
        self,
        changes: dict[str, bytes | None],
        expected: dict[str, str | None] | None = None,
        operation: str = "apply",
        crash_after: int | None = None,
        transaction_id: str | None = None,
    ) -> str | None:
        self.clean_state()
        self.gc(dry=False)
        expected = expected or {}
        for rel, fingerprint in expected.items():
            if digest(safe(self.root, rel)) != fingerprint:
                raise Invalid(f"CONCURRENT_EDIT: {rel}")
        changed: dict[str, bytes | None] = {}
        before: dict[str, bytes | None] = {}
        for rel, value in changes.items():
            path = safe(self.root, rel)
            raw = path.read_bytes() if path.is_file() else None
            if raw != value:
                changed[rel] = value
                before[rel] = raw
        if not changed:
            return None
        needed = sum(len(before[k] or b"") + len(v or b"") for k, v in changed.items()) + 16384
        if needed > self.budget:
            raise Invalid(f"RETENTION_BUDGET: operation requires at least {needed} bytes")
        if shutil.disk_usage(self.root).free < needed + self.headroom:
            raise Invalid(f"INSUFFICIENT_SPACE: need {needed + self.headroom} free bytes before staging")

        rs = self.receipts()
        while rs and sum(v[2] for v in rs) + needed > self.budget:
            if len(rs) <= 1:
                raise Invalid("RETENTION_BUDGET: increase budget; latest rollback must be retained during commit")
            shutil.rmtree(rs[0][0])
            rs = self.receipts()

        txid = transaction_id or self.new_id()
        if not re.fullmatch(r"\d{20}-[0-9a-f]{12}", txid):
            raise Invalid("Unsafe transaction identity")
        folder = self.txns / txid
        folder.mkdir(parents=True)
        receipt = {"id": txid, "operation": operation, "created": time.time(), "files": []}
        for i, (rel, value) in enumerate(changed.items()):
            slot = f"before-{i:06d}.bin"
            receipt["files"].append(
                {
                    "path": rel,
                    "slot": slot,
                    "before": sha(before[rel]) if before[rel] is not None else None,
                    "after": sha(value) if value is not None else None,
                }
            )
        raw = json_bytes(receipt)
        durable(folder / "receipt.json", raw)
        durable(folder / "receipt.sha256", sha(raw).encode("utf-8"))

        total = len(changed)
        bulk = total > _BULK_THRESHOLD
        tick = time.monotonic()
        with _bulk_write(bulk):
            for i, (rel, value) in enumerate(changed.items()):
                if before[rel] is not None:
                    durable(folder / f"before-{i:06d}.bin", before[rel])
                if value is not None:
                    durable(folder / f"after-{i:06d}.bin", value)
                tick = _report_progress(operation, "staging", i + 1, total, tick)
        durable(folder / "ready", b"all backups and staged writes durable\n")

        for rel, fingerprint in expected.items():
            if digest(safe(self.root, rel)) != fingerprint:
                durable(folder / "cancelled", b"concurrent edit\n")
                durable(folder / "complete", b"cancelled\n")
                raise Invalid(f"CONCURRENT_EDIT: {rel}; no promotion")

        tick = time.monotonic()
        with _bulk_write(bulk):
            for i, item in enumerate(receipt["files"]):
                path = safe(self.root, item["path"])
                if digest(path) != item["before"]:
                    raise Invalid(f"CONCURRENT_EDIT: {item['path']}; recover required")
                if item["after"] is None:
                    path.unlink(missing_ok=True)
                    _prune_empty_parents(path, self.root)
                else:
                    durable(path, (folder / f"after-{i:06d}.bin").read_bytes())
                tick = _report_progress(operation, "promoting", i + 1, total, tick)
                if crash_after is not None and i + 1 == crash_after:
                    raise Invalid("SIMULATED_CRASH: promotion interrupted")

        for item in receipt["files"]:
            if digest(safe(self.root, item["path"])) != item["after"]:
                raise Invalid("Verification failed after promotion; recover required")
        durable(folder / "complete", b"verified\n")
        for p in folder.glob("after-*.bin"):
            p.unlink()
        self.gc(dry=False)
        return txid

    def recover(self) -> list[str]:
        rs = self.receipts()
        done: list[str] = []
        for folder, receipt, _, complete in rs:
            if complete:
                continue
            if not (folder / "ready").exists():
                if any(digest(safe(self.root, f["path"])) != f["before"] for f in receipt["files"]):
                    raise Invalid("Unexpected live edit during incomplete staging; preserving journal")
                shutil.rmtree(folder)
                done.append(folder.name)
                continue
            self._restore(folder, receipt)
            durable(folder / "complete", b"recovered\n")
            for p in folder.glob("after-*.bin"):
                p.unlink()
            done.append(folder.name)
        return done

    def _restore(self, folder: Path, receipt: dict) -> None:
        for item in receipt["files"]:
            if digest(safe(self.root, item["path"])) not in {item["before"], item["after"]}:
                raise Invalid(f"CONCURRENT_EDIT: cannot restore {item['path']}; preserve external edits")
        total = len(receipt["files"])
        tick = time.monotonic()
        with _bulk_write(total > _BULK_THRESHOLD):
            for i, item in enumerate(reversed(receipt["files"])):
                path = safe(self.root, item["path"])
                if item["before"] is None:
                    path.unlink(missing_ok=True)
                    _prune_empty_parents(path, self.root)
                else:
                    durable(path, (folder / item["slot"]).read_bytes())
                tick = _report_progress("recover", "restoring", i + 1, total, tick)

    def rollback(self, snapshot_id: str | None = None) -> str | None:
        self.clean_state()
        rs = self.receipts()
        if not rs:
            raise Invalid("No retained transaction to roll back")
        folder, receipt, _, _ = rs[-1]
        if snapshot_id is not None and folder.name != snapshot_id:
            raise Invalid(f"ROLLBACK_DENIED: only latest snapshot {folder.name} can be rolled back safely without losing newer progress")
        if (folder / "cancelled").exists() or (folder / "complete").read_bytes() != b"verified\n":
            raise Invalid("Latest receipt was cancelled or recovered; inspect before rollback")
        expected = {v["path"]: v["after"] for v in receipt["files"]}
        changes = {
            v["path"]: ((folder / v["slot"]).read_bytes() if v["before"] is not None else None)
            for v in receipt["files"]
        }
        return self.commit(changes, expected, operation="rollback")
