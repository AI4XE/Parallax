#!/usr/bin/env python3
"""Run an argv transaction outside the caller tree; privately journal its result."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import proc_compat  # noqa: E402  (local stdlib-only helper)

LAUNCH_TIMEOUT = 10.0
PRIVATE_ENV = {'SWITCH_DETACHED', 'MSWITCH_DETACHED', 'CSWITCH_MANAGED',
               'CSWITCH_EXPECT_SOURCE', 'CSWITCH_EXPECT_SID', 'CSWITCH_EXPECT_IDENTITY',
               'CSWITCH_SUMMARY_FILE', 'DETACHED_EXEC_WORKER_PID', 'DETACHED_EXEC_WORKER_START'}


def identity(pid):
    """Read process identity (ppid, start token, state) portably.

    Uses the shared compat layer: /proc on Linux/WSL (unchanged semantics),
    `ps`/lstart on macOS. Returns None when the process cannot be identified.
    """
    info = proc_compat.lookup(pid)
    if info is None:
        return None
    token = proc_compat.start_token(pid)
    if token is None:
        return None
    return info.ppid, token, info.stat[:1]


def check_parent():
    """Accept only a live session-owning worker's immediate shell controller.

    Environment markers alone are never proof. Recheck identities after reading
    session IDs so exit/reparent/PID reuse races fail closed.
    """
    try:
        worker_pid = int(os.environ.get("DETACHED_EXEC_WORKER_PID", "0"))
        start = os.environ.get("DETACHED_EXEC_WORKER_START", "")
        controller = os.getppid()
        if worker_pid <= 1 or controller <= 1 or not start:
            return False
        parent = identity(controller)
        worker_info = identity(worker_pid)
        if (parent is None or worker_info is None or parent[0] != worker_pid
                or worker_info[1] != start or parent[2] == "Z" or worker_info[2] == "Z"):
            return False
        if os.getsid(worker_pid) != worker_pid or os.getsid(controller) != worker_pid:
            return False
        return (os.getppid() == controller and identity(controller) == parent
                and identity(worker_pid) == worker_info)
    except (OSError, ValueError, proc_compat.Unavailable):
        return False


def ancestors():
    result = set()
    pid = os.getpid()
    while pid > 1 and pid not in result:
        result.add(pid)
        try:
            info = identity(pid)
        except proc_compat.Unavailable:
            raise RuntimeError()
        if info is None:
            raise RuntimeError()
        pid = info[0]
    return result


def atomic_status(path, record):
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _open_fds():
    """List open file descriptors portably (``/dev/fd`` exists on Linux/macOS)."""
    try:
        names = os.listdir("/dev/fd")
    except OSError:
        return range(3, 1024)
    result = []
    for name in names:
        try:
            result.append(int(name))
        except ValueError:
            continue
    return result


def worker(path, operation, command, forbidden, gate, log):
    info = identity(os.getpid())
    record = {"id": operation, "status": "launching", "exitcode": None,
              "pid": os.getpid(), "start": info[1] if info else None,
              "session": os.getsid(0)}
    try:
        # Parent reaps the intermediate before opening the gate. Do not execute
        # control while adopted by a caller-side child subreaper either.
        import select
        if not select.select([gate], [], [], LAUNCH_TIMEOUT)[0]:
            raise TimeoutError()
        if os.read(gate, 1) != b"1":
            raise RuntimeError()
        os.close(gate)
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        while ancestors() - {os.getpid()} & forbidden:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            time.sleep(0.01)
        os.setsid()
        null = os.open(os.devnull, os.O_RDONLY)
        output = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(output, 0o600)
        os.dup2(null, 0)
        os.dup2(output, 1)
        os.dup2(output, 2)
        for fd in _open_fds():
            if fd > 2:
                try:
                    os.close(fd)
                except OSError:
                    pass
        info = identity(os.getpid())
        record.update(pid=os.getpid(), start=info[1], session=os.getsid(0),
                      status="running")
        atomic_status(path, record)
        env = {key: value for key, value in os.environ.items() if key not in PRIVATE_ENV}
        env.update(DETACHED_EXEC_WORKER_PID=str(os.getpid()),
                   DETACHED_EXEC_WORKER_START=info[1])
        rc = subprocess.call(command, close_fds=True, env=env)
        record.update(status="finished", exitcode=rc if rc >= 0 else 128 - rc)
    except BaseException as error:
        # Never persist exception messages, environment, or command arguments.
        record.update(status="failed", exitcode=125, error=type(error).__name__)
    try:
        atomic_status(path, record)
    finally:
        os._exit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-parent", action="store_true")
    parser.add_argument("--log")
    parser.add_argument("--state-dir")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.check_parent:
        return 0 if check_parent() else 1
    if not args.log or not args.state_dir:
        parser.error("--log and --state-dir required for execution")
    command = args.command
    if not command or command[0] != "--" or len(command) < 2:
        parser.error("fixed argv required after --")
    os.umask(0o077)
    directory = Path(args.state_dir).absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    log = Path(args.log).absolute()
    log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    operation = uuid.uuid4().hex
    path = directory / (operation + ".json")
    forbidden = ancestors()
    read_gate, write_gate = os.pipe()
    intermediate = os.fork()
    if intermediate == 0:
        os.close(write_gate)
        try:
            child = os.fork()
            if child == 0:
                worker(path, operation, command[1:], forbidden, read_gate, log)
            os._exit(0)
        except BaseException:
            os._exit(125)
    os.close(read_gate)
    try:
        reap_deadline = time.monotonic() + LAUNCH_TIMEOUT
        while True:
            reaped, status = os.waitpid(intermediate, os.WNOHANG)
            if reaped:
                break
            if time.monotonic() >= reap_deadline:
                atomic_status(path, {"id": operation, "status": "failed",
                    "pid": None, "start": None, "session": None,
                    "exitcode": 125, "error": "LaunchTimeout"})
                return 125
            time.sleep(0.01)
        if not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
            atomic_status(path, {"id": operation, "status": "failed",
                "pid": None, "start": None, "session": None,
                "exitcode": 125, "error": "LaunchFailed"})
            return 125
        os.write(write_gate, b"1")
    finally:
        os.close(write_gate)
    print(f"operation={operation} journal={path}", flush=True)
    deadline = time.monotonic() + LAUNCH_TIMEOUT * 2
    while True:
        try:
            record = json.loads(path.read_text())
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                atomic_status(path, {"id": operation, "status": "failed",
                    "pid": None, "start": None, "session": None,
                    "exitcode": 125, "error": "LaunchTimeout"})
                return 125
        else:
            if record["status"] in ("finished", "failed", "lost"):
                return record["exitcode"]
            info = identity(record["pid"])
            if info is None or info[1] != record["start"] or info[2] == "Z":
                # Atomic terminal publication precedes worker exit: reread once.
                record = json.loads(path.read_text())
                if record["status"] in ("finished", "failed"):
                    return record["exitcode"]
                record.update(status="lost", exitcode=125, error="WorkerLost")
                atomic_status(path, record)
                return 125
        time.sleep(0.05)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"detached launcher failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(125)
