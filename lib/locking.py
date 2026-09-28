"""Per-workspace write lock, atomic writes and content hashing (spec §8, §63).

The lock is an exclusive advisory `flock` on `<workspace>/.lock`. It is
reentrant within a thread (tailoring holds it while calling helpers that
also take it) but exclusive across processes and threads, and it fails
with LOCK_TIMEOUT rather than racing.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from lib.errors import ResumeTailorError

if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


_local = threading.local()  # per-thread: {lock_path: (fd, depth)}


def _held() -> dict:
    if not hasattr(_local, "held"):
        _local.held = {}
    return _local.held


@contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0, poll: float = 0.05):
    lock_path = Path(lock_path)
    key = str(lock_path.resolve())
    held = _held()
    if key in held:  # reentrant within this thread
        fd, depth = held[key]
        held[key] = (fd, depth + 1)
        try:
            yield
        finally:
            fd, depth = held[key]
            held[key] = (fd, depth - 1)
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(key, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    while not _try_lock(fd):
        if time.monotonic() >= deadline:
            os.close(fd)
            raise ResumeTailorError(
                "LOCK_TIMEOUT",
                "Another operation is writing to this workspace. Try again in a moment.",
            )
        time.sleep(poll)
    held[key] = (fd, 1)
    try:
        yield
    finally:
        del held[key]
        try:
            _unlock(fd)
        finally:
            os.close(fd)


def workspace_lock(ws, timeout: float = 10.0):
    """Exclusive lock for master/version/evidence/release writes in `ws`."""
    return file_lock(ws.lock_path, timeout=timeout)


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------

def atomic_write_bytes(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    """Write via temp file + fsync + os.replace. With exclusive=True the
    target must not already exist (checked again right before the swap)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            # link() fails if the target exists -- no silent overwrite window
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise ResumeTailorError("VERSION_EXISTS", "Target already exists; refusing to overwrite.") from None
            os.unlink(tmp)
        else:
            os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_text(path: Path, text: str, *, exclusive: bool = False) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), exclusive=exclusive)


def yaml_dump(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def atomic_write_yaml(path: Path, data, *, exclusive: bool = False) -> None:
    atomic_write_text(path, yaml_dump(data), exclusive=exclusive)


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def sha256_of(obj) -> str:
    """Hash of a data structure, independent of key order and formatting."""
    canonical = yaml.safe_dump(obj, sort_keys=True, allow_unicode=True, width=10_000)
    return sha256_text(canonical)
