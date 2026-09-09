"""Persistent opt-in exhibition resource budget. Standard library, Python 3.10+.

No API auto-initializes or resets an epoch. read_epoch is read-only, including
when latched. record_resources persists measurements/first failure before raising
EpochError, whose snapshot contains that state. A separate short file lock never
acquires the heavy-phase lease. Thread callers must drain their blocking call
before releasing any surrounding lifecycle/phase ownership after cancellation.

Files are created with mode0600. Owner execute bits are tolerated because the
external runtime volume reports owner-only files as0700; group/other access,
symlinks, hardlinks and different owners are rejected. No mount/chmod changes.
A failed write retains (or best-effort recreates after rename) a pending file,
failing closed until explicitly coordinated recovery. If storage also refuses
the failure marker, persistence cannot be guaranteed; callers must remain failed.
This is not a power-loss durability guarantee. Existing state/lock are never
deleted. An I/O error snapshot is attempted evidence, not a commit guarantee.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "logs/visual_resource_epoch.json"
SCHEMA = "lumina-resource-epoch-v1"
MINIMUM_FREE_PERCENT = 22
MAXIMUM_SWAP_GROWTH_MIB = 256
MAX_STATE_BYTES = 16384
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_KEYS = {
    "schema", "epoch_id", "baseline_scope", "baseline_swap_mib", "peak_swap_growth_mib",
    "minimum_free_percent", "maximum_swap_growth_mib", "minimum_observed_free_percent",
    "last_free_percent", "last_swap_mib", "last_sample_valid", "last_source", "source_notes",
    "blocked_reason", "created_unix", "updated_unix", "revision", "lock_identity",
}


class EpochError(RuntimeError):
    def __init__(self, code: str, *, snapshot: dict | None = None):
        self.code = code
        self.snapshot = snapshot
        super().__init__(code)


def enabled() -> bool:
    return (os.environ.get("LUMINA_VISUAL_PHASE_SERIALIZATION") == "1"
            and os.environ.get("LUMINA_VISUAL_RESOURCE_EPOCH") == "1")


def _fail(suffix: str, snapshot=None):
    raise EpochError("visual_resource_epoch_" + suffix, snapshot=snapshot)


def _number(value) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1e15 and math.isfinite(value)


def _free(value) -> bool:
    return type(value) is int and 0 <= value <= 100


def _token(value) -> bool:
    return type(value) is str and _TOKEN.fullmatch(value) is not None


def _reason(value) -> bool:
    return value is None or (type(value) is str and _REASON.fullmatch(value) is not None)


def _safe_file(fd: int):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        _fail("unsafe_file")
    return info


def _exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _same_entry(directory: int, name: str, info):
    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if (not stat.S_ISREG(current.st_mode) or current.st_dev != info.st_dev
            or current.st_ino != info.st_ino or current.st_nlink != 1):
        _fail("file_changed")


@contextmanager
def _locked(path, *, exclusive: bool, timeout: float, initialize: bool = False):
    if not _number(timeout) or timeout > 5:
        _fail("invalid_argument")
    directory = lock = None
    try:
        target = Path(path)
        target = Path(os.path.abspath(target))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", target.name):
            _fail("invalid_argument")
        # Pin an existing real directory; do not follow a redirected ancestor.
        if target.parent.resolve(strict=True) != target.parent:
            _fail("symlink")
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        lock_name, pending = target.name + ".lock", "." + target.name + ".pending"
        if initialize and (_exists(directory, target.name) or _exists(directory, lock_name)
                           or _exists(directory, pending)):
            _fail("already_exists")
        if not initialize and not _exists(directory, lock_name):
            _fail("missing_lock")
        flags = os.O_RDWR if exclusive else os.O_RDONLY
        flags |= os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        if initialize:
            flags |= os.O_CREAT | os.O_EXCL
        lock = os.open(lock_name, flags, 0o600, dir_fd=directory)
        lock_info = _safe_file(lock)
        if lock_info.st_size != 0:
            _fail("unsafe_lock")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _fail("lock_timeout")
                time.sleep(min(.01, max(0.0, deadline - time.monotonic())))
        _same_entry(directory, lock_name, lock_info)
        if _exists(directory, pending):
            _fail("pending_write")
        yield directory, target.name, pending, lock_name, lock_info
    except EpochError:
        raise
    except FileNotFoundError:
        _fail("missing")
    except FileExistsError:
        _fail("already_exists")
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _fail("symlink")
        _fail("io")
    except (TypeError, ValueError, RuntimeError):
        _fail("invalid_argument")
    finally:
        if lock is not None:
            os.close(lock)  # Closing releases flock, never removes its inode.
        if directory is not None:
            os.close(directory)


def _validate(state, lock_info, expected_epoch_id=None) -> dict:
    if not isinstance(state, dict) or set(state) != _KEYS:
        _fail("corrupt")
    if (state["schema"] != SCHEMA or state["baseline_scope"] != "shared_exhibition_epoch"
            or not _token(state["epoch_id"]) or not _reason(state["blocked_reason"])
            or type(state["source_notes"]) is not str or len(state["source_notes"]) > 512
            or (state["last_source"] is not None and not _token(state["last_source"]))
            or type(state["revision"]) is not int or not 0 <= state["revision"] < 2**63
            or type(state["minimum_free_percent"]) is not int or state["minimum_free_percent"] != MINIMUM_FREE_PERCENT
            or type(state["maximum_swap_growth_mib"]) is not int or state["maximum_swap_growth_mib"] != MAXIMUM_SWAP_GROWTH_MIB):
        _fail("corrupt")
    for key in ("baseline_swap_mib", "peak_swap_growth_mib", "created_unix", "updated_unix"):
        if not _number(state[key]):
            _fail("corrupt")
    for key in ("last_free_percent", "minimum_observed_free_percent"):
        if state[key] is not None and not _free(state[key]):
            _fail("corrupt")
    if (state["last_swap_mib"] is not None and not _number(state["last_swap_mib"])) or (
            state["last_sample_valid"] is not None and type(state["last_sample_valid"]) is not bool):
        _fail("corrupt")
    if state["updated_unix"] < state["created_unix"]:
        _fail("corrupt")
    if state["last_sample_valid"] is True and (state["last_free_percent"] is None or state["last_swap_mib"] is None):
        _fail("corrupt")
    if state["revision"] == 0:
        if any(state[key] is not None for key in ("last_sample_valid", "last_source", "last_free_percent",
                                                "last_swap_mib", "minimum_observed_free_percent")):
            _fail("corrupt")
    elif (type(state["last_sample_valid"]) is not bool or state["last_source"] is None
          or (state["last_sample_valid"] is False
              and state["last_free_percent"] is not None and state["last_swap_mib"] is not None)):
        _fail("corrupt")
    minimum = state["minimum_observed_free_percent"]
    if state["last_free_percent"] is not None and (minimum is None or minimum > state["last_free_percent"]):
        _fail("corrupt")
    if state["last_swap_mib"] is not None and state["peak_swap_growth_mib"] + 1e-6 < state["last_swap_mib"] - state["baseline_swap_mib"]:
        _fail("corrupt")
    if state["blocked_reason"] is None and (state["peak_swap_growth_mib"] > MAXIMUM_SWAP_GROWTH_MIB
            or (minimum is not None and minimum < MINIMUM_FREE_PERCENT) or state["last_sample_valid"] is False):
        _fail("corrupt")
    identity = state["lock_identity"]
    if (not isinstance(identity, dict) or set(identity) != {"device", "inode"}
            or any(type(identity[key]) is not int or identity[key] < 0 for key in identity)):
        _fail("corrupt")
    if identity != {"device": lock_info.st_dev, "inode": lock_info.st_ino}:
        _fail("lock_changed")
    if expected_epoch_id is not None:
        if not _token(expected_epoch_id):
            _fail("invalid_argument")
        if state["epoch_id"] != expected_epoch_id:
            _fail("epoch_changed")
    return state


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("corrupt")
        result[key] = value
    return result


def _read(directory, name, lock_info, expected_epoch_id=None):
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = _safe_file(fd)
        if info.st_size > MAX_STATE_BYTES:
            _fail("oversized")
        data = os.read(fd, MAX_STATE_BYTES + 1)
        if len(data) > MAX_STATE_BYTES:
            _fail("oversized")
        _same_entry(directory, name, info)
        try:
            state = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                               parse_constant=lambda _value: _fail("corrupt"))
        except (ValueError, UnicodeError):
            _fail("corrupt")
        return _validate(state, lock_info, expected_epoch_id), info
    finally:
        os.close(fd)


def _preserve_failure_marker(directory, pending, state):
    """Never replace an existing marker, symlink or any other directory entry."""
    fd = None
    failed = dict(state, blocked_reason=state["blocked_reason"] or "visual_resource_epoch_io")
    try:
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        _safe_file(fd)
        data = memoryview((json.dumps(failed, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())
        while data:
            count = os.write(fd, data)
            if count <= 0:
                break  # Even an empty/partial marker forces every future read closed.
            data = data[count:]
        os.fsync(fd)
    except (OSError, EpochError):
        pass  # Existing evidence is retained; total storage failure is not repairable here.
    finally:
        if fd is not None:
            os.close(fd)


def _write(directory, name, pending, lock_name, lock_info, state, previous):
    data = (json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_STATE_BYTES:
        _fail("oversized", state)
    fd = None
    try:
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        _safe_file(fd)
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                _fail("io", state)
            remaining = remaining[written:]
        os.fsync(fd)
        _same_entry(directory, lock_name, lock_info)
        _same_entry(directory, name, previous)
        os.replace(pending, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except EpochError:
        raise
    except OSError:
        # rename consumes the staging path. A subsequent directory-fsync error
        # must not let a healthy-looking committed sample hide the I/O failure.
        _preserve_failure_marker(directory, pending, state)
        _fail("io", state)
    finally:
        if fd is not None:
            os.close(fd)
        # Failure retains the exclusively created pending file as a denial
        # marker/evidence. Never retry, remove it, reset or restore an old state.


def initialize_epoch(baseline_swap_mib, *, initial_peak_growth_mib=0, blocked_reason=None,
                     source_notes="", epoch_id=None, path=DEFAULT_PATH, lock_timeout=1.0) -> dict:
    """Explicit one-time creation; refuse any existing state/lock/pending file."""
    epoch_id = uuid.uuid4().hex if epoch_id is None else epoch_id
    if (not _number(baseline_swap_mib) or not _number(initial_peak_growth_mib) or not _reason(blocked_reason)
            or not _token(epoch_id) or type(source_notes) is not str or len(source_notes) > 512):
        _fail("invalid_argument")
    now = time.time()
    if not _number(now):
        _fail("clock_unavailable")
    with _locked(path, exclusive=True, timeout=lock_timeout, initialize=True) as parts:
        directory, name, pending, lock_name, lock_info = parts
        if blocked_reason is None and initial_peak_growth_mib > MAXIMUM_SWAP_GROWTH_MIB:
            blocked_reason = "visual_resource_epoch_swap_growth_above_limit"
        state = {"schema": SCHEMA, "epoch_id": epoch_id, "baseline_scope": "shared_exhibition_epoch",
                 "baseline_swap_mib": float(baseline_swap_mib), "peak_swap_growth_mib": float(initial_peak_growth_mib),
                 "minimum_free_percent": MINIMUM_FREE_PERCENT, "maximum_swap_growth_mib": MAXIMUM_SWAP_GROWTH_MIB,
                 "minimum_observed_free_percent": None, "last_free_percent": None, "last_swap_mib": None,
                 "last_sample_valid": None, "last_source": None, "source_notes": source_notes,
                 "blocked_reason": blocked_reason, "created_unix": now, "updated_unix": now, "revision": 0,
                 "lock_identity": {"device": lock_info.st_dev, "inode": lock_info.st_ino}}
        _validate(state, lock_info)
        # Reserve an exclusively NEW destination, then atomically replace only
        # that reservation. This works on volumes without hardlink support.
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            previous = _safe_file(fd)
        finally:
            os.close(fd)
        _write(directory, name, pending, lock_name, lock_info, state, previous)
        return state


def read_epoch(*, path=DEFAULT_PATH, expected_epoch_id=None, lock_timeout=1.0) -> dict:
    """Read validated state, including an existing latch; never initialize."""
    with _locked(path, exclusive=False, timeout=lock_timeout) as parts:
        state, _info = _read(parts[0], parts[1], parts[4], expected_epoch_id)
        return state


def record_resources(free, swap, *, source, expected_epoch_id=None, path=DEFAULT_PATH, lock_timeout=1.0) -> dict:
    """Serial atomic update from fresh readings, retaining baseline/peak/latch."""
    if not _token(source):
        _fail("invalid_argument")
    with _locked(path, exclusive=True, timeout=lock_timeout) as parts:
        directory, name, pending, lock_name, lock_info = parts
        state, previous = _read(directory, name, lock_info, expected_epoch_id)
        old_reason = state["blocked_reason"]
        valid_free, valid_swap = _free(free), _number(swap)
        state.update(last_free_percent=free if valid_free else None, last_swap_mib=float(swap) if valid_swap else None,
                     last_sample_valid=valid_free and valid_swap, last_source=source)
        if valid_free:
            old_min = state["minimum_observed_free_percent"]
            state["minimum_observed_free_percent"] = free if old_min is None else min(old_min, free)
        if valid_swap:
            state["peak_swap_growth_mib"] = max(state["peak_swap_growth_mib"], swap - state["baseline_swap_mib"])
        reason = ("visual_resource_epoch_reading_unavailable" if not (valid_free and valid_swap)
                  else "visual_resource_epoch_free_below_limit" if free < MINIMUM_FREE_PERCENT
                  else "visual_resource_epoch_swap_growth_above_limit" if state["peak_swap_growth_mib"] > MAXIMUM_SWAP_GROWTH_MIB else None)
        state["blocked_reason"] = old_reason or reason
        now = time.time()
        if not _number(now):
            state["blocked_reason"] = old_reason or "visual_resource_epoch_clock_unavailable"
        else:
            state["updated_unix"] = max(state["updated_unix"], now)
        state["revision"] += 1
        _validate(state, lock_info, expected_epoch_id)
        _write(directory, name, pending, lock_name, lock_info, state, previous)
        if state["blocked_reason"]:
            raise EpochError("visual_resource_epoch_blocked" if old_reason else state["blocked_reason"], snapshot=state)
        return state
