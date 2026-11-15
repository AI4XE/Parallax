"""Cross-platform process compatibility layer (Linux / WSL / macOS).

Pure standard library. The process table comes from the system ``ps`` (present
on every supported platform); ``/proc`` is used only where it exists and is
strictly faster or richer (Linux/WSL), with ``lsof`` as the macOS fallback for
the working directory.

Design rules:

* Never guess. When a fact cannot be determined the module raises
  :class:`Unavailable`; callers must fail closed instead of assuming "dead".
* "Not in a cached snapshot" never means "process does not exist": single-pid
  lookups always run a directed probe.
* ``comm`` is normalised to its basename because macOS returns a full path.
* Python 3.8 compatible: annotations are postponed so no runtime evaluation of
  PEP 585 generics happens.
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, List, Optional, Tuple


class Unavailable(Exception):
    """A process fact could not be determined (distinct from "process dead")."""


class Info(object):
    """One row of the process table."""

    __slots__ = ("pid", "ppid", "stat", "comm")

    def __init__(self, pid: int, ppid: int, stat: str, comm: str) -> None:
        self.pid = pid
        self.ppid = ppid
        self.stat = stat
        self.comm = comm

    @property
    def dead(self) -> bool:
        return self.stat[:1] in ("Z", "X")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Info(pid=%d, ppid=%d, stat=%r, comm=%r)" % (
            self.pid, self.ppid, self.stat, self.comm)


_TABLE_ARGV = ("ps", "-axo", "pid=,ppid=,stat=,comm=")
_PROBE_HINT = (
    "system `ps` is unusable; install procps (Linux) or use the built-in ps "
    "(macOS/WSL) so process identity can be verified")


def _ps_env() -> Dict[str, str]:
    env = {"LC_ALL": "C", "LANG": "C"}
    for key in ("PATH", "SYSTEMROOT"):
        if key in os.environ:
            env[key] = os.environ[key]
    if "PATH" not in env:
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def _run_ps(argv, timeout: float = 5.0) -> Tuple[int, str]:
    """Run one ``ps`` invocation.

    Returns ``(returncode, stdout)``. Missing/timed-out/failed ``ps`` raises
    :class:`Unavailable`; a non-zero code with no output is left to the caller
    to interpret as "no such process".
    """
    try:
        result = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_ps_env(),
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise Unavailable(_PROBE_HINT)
    return result.returncode, result.stdout.decode("utf-8", "replace")


def _read_proc_file(pid: int, name: str) -> bytes:
    """Read ``/proc/<pid>/<name>`` as bytes (test seam)."""
    with open("/proc/%d/%s" % (int(pid), name), "rb") as stream:
        return stream.read()


def _read_proc_link(pid: int, name: str) -> str:
    """Read a ``/proc/<pid>/<name>`` symlink target (test seam)."""
    return os.readlink("/proc/%d/%s" % (int(pid), name))


def _basename(comm: str) -> str:
    value = (comm or "").strip()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return value


def _parse_table(text: str) -> Dict[int, Info]:
    table = {}
    for line in text.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except (TypeError, ValueError):
            continue
        table[pid] = Info(pid, ppid, parts[2], _basename(parts[3]))
    return table


_cache = {"table": None, "at": 0.0}
_probed = False


def reset_cache() -> None:
    """Drop the snapshot cache and the probe flag (tests, or after fork)."""
    global _probed
    _cache["table"] = None
    _cache["at"] = 0.0
    _probed = False


def probe() -> None:
    """Verify ``ps`` is usable: it must parse and include this very process."""
    global _probed
    code, out = _run_ps(_TABLE_ARGV)
    if code != 0:
        raise Unavailable(_PROBE_HINT)
    table = _parse_table(out)
    if not table or os.getpid() not in table:
        raise Unavailable(_PROBE_HINT)
    _cache["table"] = table
    _cache["at"] = time.monotonic()
    _probed = True


def _ensure() -> None:
    if not _probed:
        probe()


def _pointed(pid: int) -> Optional[Info]:
    code, out = _run_ps(("ps", "-ww", "-p", str(int(pid)), "-o", "pid=,ppid=,stat=,comm="))
    if code != 0:
        return None
    return _parse_table(out).get(int(pid))


def snapshot(ttl: float = 0.5, fresh: bool = False) -> Dict[int, Info]:
    """Full process table, short-TTL cached. For batch enumeration only."""
    _ensure()
    now = time.monotonic()
    if not fresh and _cache["table"] is not None and now - _cache["at"] <= ttl:
        return _cache["table"]
    code, out = _run_ps(_TABLE_ARGV)
    if code != 0:
        raise Unavailable(_PROBE_HINT)
    table = _parse_table(out)
    if not table:
        raise Unavailable(_PROBE_HINT)
    _cache["table"] = table
    _cache["at"] = now
    return table


def lookup(pid: int, fresh: bool = True) -> Optional[Info]:
    """Single-pid lookup. Fresh by default; cache misses always get a directed probe."""
    _ensure()
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if not fresh:
        table = _cache["table"]
        if table is not None and pid in table:
            return table[pid]
    return _pointed(pid)


def args(pid: int) -> str:
    """Full command line of ``pid`` (empty string when unavailable)."""
    _ensure()
    code, out = _run_ps(("ps", "-ww", "-p", str(int(pid)), "-o", "args="))
    if code != 0:
        return ""
    return out.strip()


def descendants(pid: int, fresh: bool = False) -> List[int]:
    """All transitive children of ``pid``."""
    table = snapshot(fresh=fresh)
    children = {}
    for info in table.values():
        children.setdefault(info.ppid, []).append(info.pid)
    result = []
    seen = set()
    stack = [int(pid)]
    while stack:
        current = stack.pop()
        for child in children.get(current, ()):
            if child in seen:
                continue
            seen.add(child)
            result.append(child)
            stack.append(child)
    return result


def alive(pid: int, expect_sub: Optional[str] = None, fresh: bool = True) -> bool:
    """True when ``pid`` is running and not a zombie.

    ``expect_sub`` must match the full command line (``ps args``), never the
    short ``comm`` name, so PID reuse across a different program is rejected.
    Raises :class:`Unavailable` when liveness cannot be determined.
    """
    info = lookup(pid, fresh=fresh)
    if info is None:
        return False
    if info.dead:
        return False
    if expect_sub:
        command = args(pid)
        if expect_sub not in command:
            return False
    return True


def start_token(pid: int) -> Optional[str]:
    """A per-process start marker.

    Linux/WSL keeps the historical ``/proc/<pid>/stat`` field 22 string
    (byte-identical to the previous behaviour). macOS derives a second-precision
    epoch from ``ps -o lstart=`` and prefixes it with ``epoch:``.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        text = _read_proc_file(pid, "stat").decode("utf-8", "replace")
        fields = text.rsplit(")", 1)[1].split()
        return fields[19]
    except (OSError, IndexError, ValueError):
        pass
    _ensure()
    code, out = _run_ps(("ps", "-ww", "-p", str(pid), "-o", "lstart="))
    if code != 0:
        return None
    normalized = " ".join(out.split())
    if not normalized:
        return None
    try:
        epoch = int(time.mktime(time.strptime(normalized, "%a %b %d %H:%M:%S %Y")))
    except (ValueError, OverflowError):
        return None
    return "epoch:%d" % epoch


def start_matches(expected, actual, started_at_ms=None) -> bool:
    """Compare a recorded start token with the live one.

    Linux tokens compare byte-for-byte (unchanged behaviour). A macOS
    ``epoch:<sec>`` token can never equal a numeric Linux token, so it uses a
    one-sided upper bound: the registration timestamp (``started_at_ms``) may
    trail the real process start by 0..300s. A negative gap means a stale record
    or PID reuse and is always rejected.
    """
    if expected is None or actual is None:
        return False
    expected = str(expected)
    actual = str(actual)
    if expected == actual:
        return True
    if not actual.startswith("epoch:"):
        return False
    if started_at_ms is None:
        return False
    try:
        process_epoch = int(actual.split(":", 1)[1])
        registered = float(started_at_ms) / 1000.0
    except (TypeError, ValueError):
        return False
    gap = registered - process_epoch
    return 0 <= gap <= 300


def cwd(pid: int) -> Optional[str]:
    """Working directory of ``pid`` (``/proc`` on Linux/WSL, ``lsof`` on macOS)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        return _read_proc_link(pid, "cwd")
    except OSError:
        pass
    return _lsof_cwd(pid)


def _lsof_cwd(pid: int) -> Optional[str]:
    try:
        result = subprocess.run(
            ("lsof", "-a", "-p", str(int(pid)), "-d", "cwd", "-Fn"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_ps_env(),
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def environ(pid: int) -> Optional[Dict[str, str]]:
    """Environment of ``pid``; ``None`` when unreadable (macOS: always None)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        data = _read_proc_file(pid, "environ")
    except OSError:
        return None
    result = {}
    for item in data.split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator:
            result[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return result
