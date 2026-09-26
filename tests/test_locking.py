"""Per-workspace lock, atomic writes, hashing."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from lib import locking
from lib.errors import ResumeTailorError
from lib.locking import atomic_write_bytes, atomic_write_yaml, file_lock, sha256_of, workspace_lock

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = str(REPO_ROOT / ".venv" / "bin" / "python")
if not Path(PY).exists():
    PY = sys.executable

HOLDER = r"""
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
print("READY", flush=True)
time.sleep(float(sys.argv[3]))
"""


def _code(excinfo) -> str:
    return excinfo.value.code


def _tmp_leftovers(d: Path) -> list[Path]:
    return [p for p in d.iterdir() if p.name.endswith(".tmp")]


def _start_holder(lock: Path, ready: Path, hold: float) -> subprocess.Popen:
    proc = subprocess.Popen([PY, "-c", HOLDER, str(lock), str(ready), str(hold)],
                            stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline().strip()
    assert line == "READY" and ready.exists(), "holder process did not acquire the lock"
    return proc


# --------------------------------------------------------------------------
# Locks
# --------------------------------------------------------------------------

def test_reentrant_in_same_thread(tmp_path):
    lock = tmp_path / ".lock"
    with file_lock(lock, timeout=0.2):
        with file_lock(lock, timeout=0.2):
            with file_lock(lock, timeout=0.2):
                pass
        # still held after inner exits: another thread cannot take it
        result = {}

        def other():
            try:
                with file_lock(lock, timeout=0.2):
                    result["got"] = True
            except ResumeTailorError as e:
                result["code"] = e.code

        t = threading.Thread(target=other)
        t.start()
        t.join()
        assert result == {"code": "LOCK_TIMEOUT"}


def test_reentrant_via_different_path_spelling(tmp_path):
    lock = tmp_path / ".lock"
    (tmp_path / "sub").mkdir()
    alias = tmp_path / "sub" / ".." / ".lock"
    with file_lock(lock, timeout=0.2):
        with file_lock(alias, timeout=0.2):
            pass


def test_other_thread_times_out(tmp_path):
    lock = tmp_path / ".lock"
    result = {}

    def other():
        t0 = time.monotonic()
        try:
            with file_lock(lock, timeout=0.3):
                result["got"] = True
        except ResumeTailorError as e:
            result["code"] = e.code
            result["elapsed"] = time.monotonic() - t0

    with file_lock(lock):
        t = threading.Thread(target=other)
        t.start()
        t.join(5)
    assert result.get("code") == "LOCK_TIMEOUT"
    assert result["elapsed"] >= 0.25  # it actually waited


def test_other_thread_gets_lock_after_release(tmp_path):
    lock = tmp_path / ".lock"
    order = []
    started = threading.Event()

    def other():
        started.set()
        with file_lock(lock, timeout=5):
            order.append("other")

    with file_lock(lock):
        t = threading.Thread(target=other)
        t.start()
        started.wait()
        time.sleep(0.2)
        order.append("main")
    t.join(5)
    assert order == ["main", "other"]


def test_other_process_causes_timeout(tmp_path):
    lock = tmp_path / ".lock"
    ready = tmp_path / "ready"
    proc = _start_holder(lock, ready, hold=5)
    try:
        with pytest.raises(ResumeTailorError) as ei:
            with file_lock(lock, timeout=0.3):
                pass
        assert _code(ei) == "LOCK_TIMEOUT"
    finally:
        proc.kill()
        proc.wait()
    # once the holder is gone the lock is free again
    with file_lock(lock, timeout=2):
        pass


def test_other_process_blocks_workspace_writes(workspace, tmp_path):
    from lib import storage
    ready = tmp_path / "ready"
    proc = _start_holder(workspace.lock_path, ready, hold=5)
    try:
        with pytest.raises(ResumeTailorError) as ei:
            with workspace_lock(workspace, timeout=0.3):
                pass
        assert _code(ei) == "LOCK_TIMEOUT"
    finally:
        proc.kill()
        proc.wait()
    storage.save_jd("jd1", "text", ws=workspace)


def test_lock_released_after_exception(tmp_path):
    lock = tmp_path / ".lock"

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with file_lock(lock):
            raise Boom()
    assert locking._held() == {}
    # another thread can take it immediately
    ok = []

    def grab():
        with file_lock(lock, timeout=0.3):
            ok.append(1)

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    assert ok == [1]


def test_lock_released_after_exception_in_reentrant_inner(tmp_path):
    lock = tmp_path / ".lock"
    with pytest.raises(ValueError):
        with file_lock(lock):
            with file_lock(lock):
                raise ValueError()
    assert locking._held() == {}
    ok = []

    def grab():
        with file_lock(lock, timeout=0.3):
            ok.append(1)

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    assert ok == [1]


def test_timeout_does_not_leak_fd_or_held_state(tmp_path):
    lock = tmp_path / ".lock"
    errors = []

    def contender():
        for _ in range(5):
            try:
                with file_lock(lock, timeout=0.05):
                    pass
            except ResumeTailorError as e:
                errors.append(e.code)
        errors.append(dict(locking._held()))

    with file_lock(lock):
        t = threading.Thread(target=contender)
        t.start()
        t.join()
    assert errors[:5] == ["LOCK_TIMEOUT"] * 5
    assert errors[5] == {}


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------

def test_atomic_write_creates_parents_and_content(tmp_path):
    target = tmp_path / "a" / "b" / "c.bin"
    atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"
    assert _tmp_leftovers(target.parent) == []


def test_atomic_write_overwrites_non_exclusive(tmp_path):
    target = tmp_path / "f"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"
    assert _tmp_leftovers(tmp_path) == []


def test_exclusive_refuses_existing_and_keeps_original(tmp_path):
    target = tmp_path / "f.yaml"
    target.write_bytes(b"original")
    with pytest.raises(ResumeTailorError) as ei:
        atomic_write_bytes(target, b"clobber", exclusive=True)
    assert _code(ei) == "VERSION_EXISTS"
    assert target.read_bytes() == b"original"
    assert _tmp_leftovers(tmp_path) == []


def test_exclusive_writes_new_file(tmp_path):
    target = tmp_path / "new.yaml"
    atomic_write_bytes(target, b"x", exclusive=True)
    assert target.read_bytes() == b"x"
    assert _tmp_leftovers(tmp_path) == []


def test_no_temp_left_when_serialization_fails(tmp_path):
    target = tmp_path / "f.yaml"
    target.write_text("keep: 1\n")
    with pytest.raises(Exception):
        atomic_write_yaml(target, {"bad": object()})  # safe_dump refuses arbitrary objects
    assert target.read_text() == "keep: 1\n"
    assert _tmp_leftovers(tmp_path) == []


def test_no_temp_left_when_write_fails(tmp_path, monkeypatch):
    target = tmp_path / "f.bin"
    target.write_bytes(b"keep")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(locking.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"keep"
    assert _tmp_leftovers(tmp_path) == []


def test_many_writes_leave_no_temp(tmp_path):
    target = tmp_path / "f.yaml"
    for i in range(30):
        atomic_write_yaml(target, {"i": i})
    assert target.read_text() == "i: 29\n"
    assert [p.name for p in tmp_path.iterdir()] == ["f.yaml"]


def test_concurrent_exclusive_writes_one_wins(tmp_path):
    target = tmp_path / "v.yaml"
    results = []
    barrier = threading.Barrier(8)

    def writer(i):
        barrier.wait()
        try:
            atomic_write_bytes(target, f"w{i}".encode(), exclusive=True)
            results.append(("ok", i))
        except ResumeTailorError as e:
            results.append((e.code, i))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [i for s, i in results if s == "ok"]
    assert len(winners) == 1
    assert all(s in ("ok", "VERSION_EXISTS") for s, _ in results)
    assert target.read_bytes() == f"w{winners[0]}".encode()
    assert _tmp_leftovers(tmp_path) == []


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def test_sha256_of_ignores_key_order():
    a = {"b": 1, "a": {"y": [1, 2], "x": "s"}}
    b = {"a": {"x": "s", "y": [1, 2]}, "b": 1}
    assert sha256_of(a) == sha256_of(b)


def test_sha256_of_is_sensitive_to_content_and_list_order():
    base = {"a": [1, 2]}
    assert sha256_of(base) != sha256_of({"a": [2, 1]})
    assert sha256_of(base) != sha256_of({"a": [1, 2, 3]})
    assert sha256_of({"a": "1"}) != sha256_of({"a": 1})
    assert sha256_of(None) != sha256_of({})


def test_sha256_of_long_strings_not_wrapped_equal():
    # canonical dump must not collapse distinct long strings
    s1 = "word " * 5000
    s2 = "word " * 4999 + "word!"
    assert sha256_of({"s": s1}) != sha256_of({"s": s2})
