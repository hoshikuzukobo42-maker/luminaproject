"""Process supervision helpers: stale PID cleanup and child exit detection."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    command_needle: str
    pid_file: Path
    started_at: float


@dataclass
class ProcessSupervisor:
    records: dict[str, ProcessRecord]

    def __init__(self) -> None:
        self.records = {}

    @staticmethod
    def pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def read_pid_file(path: Path) -> int | None:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8").strip()
        if not raw.isdigit():
            return None
        return int(raw)

    def register(self, name: str, *, pid: int, command_needle: str, pid_file: Path) -> None:
        self.records[name] = ProcessRecord(
            pid=pid,
            command_needle=command_needle,
            pid_file=pid_file,
            started_at=time.time(),
        )
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(f"{pid}\n", encoding="utf-8")

    def clear_stale_pid_file(self, name: str) -> bool:
        record = self.records.get(name)
        if record is None and name not in self.records:
            return False
        if record is None:
            return False
        if self.pid_alive(record.pid) and self._command_matches(record.pid, record.command_needle):
            return False
        if record.pid_file.exists():
            record.pid_file.unlink(missing_ok=True)
        return True

    @staticmethod
    def _read_command_line(pid: int) -> str | None:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            return cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
        if sys.platform == "darwin":
            try:
                proc = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2.0,
                )
            except (OSError, subprocess.SubprocessError):
                return None
            if proc.returncode == 0:
                return proc.stdout.strip()
        return None

    @staticmethod
    def _command_matches(pid: int, needle: str) -> bool:
        if not needle:
            return True
        cmd = ProcessSupervisor._read_command_line(pid)
        if cmd is None:
            return True
        if needle in cmd:
            return True
        if sys.platform == "darwin":
            return True
        return False

    def terminate(self, name: str, *, graceful_seconds: float = 5.0) -> bool:
        record = self.records.get(name)
        if record is None:
            return True
        if not self.pid_alive(record.pid):
            record.pid_file.unlink(missing_ok=True)
            self.records.pop(name, None)
            return True
        os.kill(record.pid, signal.SIGTERM)
        deadline = time.time() + graceful_seconds
        while time.time() < deadline:
            if not self.pid_alive(record.pid):
                record.pid_file.unlink(missing_ok=True)
                self.records.pop(name, None)
                return True
            time.sleep(0.1)
        if self.pid_alive(record.pid):
            os.kill(record.pid, signal.SIGKILL)
        record.pid_file.unlink(missing_ok=True)
        self.records.pop(name, None)
        return True

    def detect_abnormal_exits(self) -> tuple[str, ...]:
        dead: list[str] = []
        for name, record in list(self.records.items()):
            if not self.pid_alive(record.pid):
                dead.append(name)
                record.pid_file.unlink(missing_ok=True)
                self.records.pop(name, None)
        return tuple(dead)

    def cleanup_all(self) -> None:
        for name in list(self.records.keys()):
            self.terminate(name)


def reconcile_pid_files(paths: Iterable[Path], *, command_needles: Mapping[str, str] | None = None) -> list[Path]:
    """Remove pid files whose process is absent or command mismatch."""

    needles = command_needles or {}
    removed: list[Path] = []
    for path in paths:
        raw = ProcessSupervisor.read_pid_file(path)
        if raw is None:
            path.unlink(missing_ok=True)
            removed.append(path)
            continue
        needle = needles.get(str(path), "")
        if not ProcessSupervisor.pid_alive(raw) or (
            needle and not ProcessSupervisor._command_matches(raw, needle)
        ):
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


__all__ = ["ProcessRecord", "ProcessSupervisor", "reconcile_pid_files"]
