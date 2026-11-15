#!/usr/bin/env python3
"""qgo D-backend executor driver — stream-json pipe carrier for team executors.

Replaces the tmux send-keys / capture-pane carrier with a headless claude
process driven over bidirectional stream-json pipes:

  orchestrator -> driver CLI -> stdin.fifo -> pump -> executor stdin  (feed)
  executor stdout -> pump -> events.log + state.json -> driver status    (observe)

Design invariants (plan §3.1):
  * All coordination state lives in files under <root>/tmp/qgo/<ID>/ — no
    process memory is trusted. Any new pump can pick up from state.json.
  * executor and pump are both setsid'd (own session, no controlling tty,
    stdio redirected) — parent death causes zero signals and zero broken
    pipes beyond the documented pump-death EPIPE cascade (handled by
    stop+resume, never live takeover).
  * Completion is judged from structured evidence (a `result` event plus a
    non-empty completion/ directory), never from screen scraping.
  * Orphan recovery main path is stop + resume; "takeover" only exists as
    bookkeeping correction for an already-dead executor.
  * Permission modes are hard-restricted to bypassPermissions (preferred)
    or dontAsk + --allowedTools. This driver implements no
    --permission-prompt-tool control protocol, so default/acceptEdits/plan
    would wedge the executor forever — `start` validates and refuses.
  * Managed-file hygiene: no machine paths, project names, or secrets in
    this file; everything is derived from --root and runtime env.

Interpreter convention (same as gclear): invoke this driver with the
interpreter chosen by CC_PYTHON -> __SD_PYTHON_RESOLVED -> project .env
*_PYTHON -> python3. The pump re-uses sys.executable. `claude` is
resolved from PATH only (no version pinning, no probing).

Python >= 3.8 compatible.
"""

import argparse
import errno
import json
import select
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import proc_compat  # noqa: E402  (local stdlib-only helper)

DRIVER_VERSION = "1.0.3"

# Message-class MCP servers never reach the executor (plan §3.1).
MCP_DENY_RE = re.compile(r"telegram|cc-connect|ccgram|feishu|wechat|slack|discord", re.I)

# Exact-name env strip list (plan §3.1 spawn discipline).
STRIP_EXACT = {"CC_SESSION_KEY", "CLAUDE_CODE_SESSION_ID", "TMUX", "TMUX_PANE"}
# Prefix/suffix strip families.
STRIP_PREFIX = ("TELEGRAM_", "ANTHROPIC_")
STRIP_SUFFIX = ("_BOT_TOKEN", "_SESSION_DRIVER")

ALLOWED_MODES = ("bypassPermissions", "dontAsk")

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

STATE_STARTING = "starting"
STATE_WORKING = "working"
STATE_IDLE = "idle"          # alive, last turn ended (result seen), no completion yet
STATE_DONE = "done"
STATE_BLOCKED = "blocked"
STATE_FAILED = "failed"
STATE_CRASHED = "crashed"
STATE_STOPPED = "stopped"
STATE_ORPHANED = "orphaned"
TERMINAL_STATES = (STATE_DONE, STATE_FAILED, STATE_CRASHED, STATE_STOPPED, STATE_ORPHANED)

DEFAULT_START_TIMEOUT = 90
DEFAULT_STOP_GRACE = 20          # seconds allowed for natural exit after stdin EOF
DEFAULT_TERM_GRACE = 10          # seconds allowed after SIGTERM before SIGKILL
COMPLETION_POLL = 30             # seconds between completion-dir rechecks


def now():
    return time.time()


def fail(msg, code=2):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(code)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def check_id(value):
    if not ID_RE.match(value or ""):
        fail("invalid executor id %r (must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63})" % value)
    return value


def qgo_dir(root, exec_id):
    return os.path.join(root, "tmp", "qgo", exec_id)


def clean_executor_env(exec_id):
    """Inherited env minus the strip list, plus QGO_EXECUTOR_ID."""
    env = {}
    for key, value in os.environ.items():
        if key in STRIP_EXACT:
            continue
        if key.startswith(STRIP_PREFIX):
            continue
        if key.endswith(STRIP_SUFFIX):
            continue
        env[key] = value
    env["QGO_EXECUTOR_ID"] = exec_id
    return env


def filter_mcp(root, dest):
    """Copy <root>/.mcp.json minus message-class servers into dest."""
    src = os.path.join(root, ".mcp.json")
    servers = {}
    if os.path.isfile(src):
        try:
            with open(src, "r", encoding="utf-8") as fh:
                servers = (json.load(fh) or {}).get("mcpServers") or {}
        except (OSError, ValueError) as exc:
            raise RuntimeError("cannot parse %s: %s" % (src, exc))
    kept = {k: v for k, v in servers.items() if not MCP_DENY_RE.search(k)}
    payload = {"mcpServers": kept}
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return len(servers), len(kept)


def proc_alive(pid, expect_sub=None):
    """True when pid runs and (optionally) its full command line contains expect_sub.

    Delegates to the portable compat layer. The command-line check guards
    against PID reuse: we never signal a process we cannot prove is ours.
    Raises proc_compat.Unavailable when liveness cannot be determined at all.
    """
    return proc_compat.alive(pid, expect_sub)


def read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_state(path, state):
    state["updated_at"] = now()
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def completion_present(d):
    comp = os.path.join(d, "completion")
    if not os.path.isdir(comp):
        return False
    for name in os.listdir(comp):
        p = os.path.join(comp, name)
        try:
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                return True
        except OSError:
            continue
    return False


def ctx_from_usage(usage):
    """Approximate current context size (tokens) from an assistant usage blob."""
    if not isinstance(usage, dict):
        return None
    def num(key):
        v = usage.get(key)
        return v if isinstance(v, (int, float)) else 0
    total = num("input_tokens") + num("cache_read_input_tokens") \
        + num("cache_creation_input_tokens") + num("output_tokens")
    return total or None


def build_executor_cmd(spec):
    cmd = [
        "claude",
        "--setting-sources", "project,local",
        "--permission-mode", spec["permission_mode"],
        "--settings",
        json.dumps({"skipDangerousModePermissionPrompt": True,
                    "promptSuggestionEnabled": False}),
        "--strict-mcp-config",
        "--mcp-config", spec["mcp_path"],
        "--disallowedTools", "Skill(gclear)",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
    ]
    if spec["permission_mode"] == "dontAsk":
        cmd += ["--allowedTools", spec["allowed_tools"]]
    if spec.get("resume_sid"):
        cmd += ["--resume", spec["resume_sid"]]
    return cmd


def validate_mode(args):
    mode = args.permission_mode
    if mode not in ALLOWED_MODES:
        fail(
            "permission mode %r is not allowed for the D-backend executor: this "
            "driver implements no --permission-prompt-tool control protocol, so "
            "interactive modes (default/acceptEdits/plan) would wedge forever. "
            "Use bypassPermissions (preferred) or dontAsk + --allowed-tools." % mode
        )
    if mode == "dontAsk" and not (args.allowed_tools or "").strip():
        fail("dontAsk mode requires --allowed-tools (strict-host whitelist)")
    return mode


def require_claude_on_path():
    if shutil.which("claude") is None:
        fail("`claude` not found on PATH (driver resolves claude from PATH only); "
             "fix the invoking environment instead of pinning versions here")


# ──────────────────────────── pump (resident carrier) ────────────────────

class Pump:
    """Holds executor stdin/stdout; translates fifo lines into user messages.

    Everything durable is written to files: state.json (atomic), events.log
    (append-only JSONL, includes driver lifecycle markers with
    type == "driver"), executor.stderr.log.
    """

    def __init__(self, exec_id, root):
        self.exec_id = exec_id
        self.root = root
        self.d = qgo_dir(root, exec_id)
        self.state_path = os.path.join(self.d, "state.json")
        self.events_path = os.path.join(self.d, "events.log")
        self.fifo_path = os.path.join(self.d, "stdin.fifo")
        self.spec_path = os.path.join(self.d, "spec.json")
        self.executor = None
        self.executor_exit = None     # (code, signal) once reaped
        self.state = None
        self.stop_ladder_started = None
        self.last_completion_check = 0.0

    # ── state helpers ────────────────────────────────────────────────────
    def load(self):
        st = read_state(self.state_path)
        if st is None:
            raise RuntimeError("state.json missing/unreadable for %s" % self.exec_id)
        self.state = st

    def save(self):
        write_state(self.state_path, self.state)

    def log_event(self, payload):
        payload = dict(payload)
        payload.setdefault("ts", now())
        line = json.dumps(payload, ensure_ascii=False)
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.state["last_event"] = payload.get("type", "unknown")
        self.state["last_event_ts"] = payload["ts"]
        if payload.get("type") == "control_request":
            self.state["state"] = STATE_BLOCKED
        self.save()

    # ── executor lifecycle ───────────────────────────────────────────────
    def spawn_executor(self):
        spec = read_state(self.spec_path)  # spec.json uses the same writer
        if not spec or "permission_mode" not in spec:
            raise RuntimeError("spec.json missing/invalid for %s" % self.exec_id)
        env = clean_executor_env(self.exec_id)
        errlog = open(os.path.join(self.d, "executor.stderr.log"), "ab", 0)
        self.executor = subprocess.Popen(
            build_executor_cmd(spec),
            cwd=self.root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errlog,
            bufsize=0,
            start_new_session=True,
        )
        self.state["executor_pid"] = self.executor.pid
        for name, pid in (("executor.pid", self.executor.pid), ("pump.pid", os.getpid())):
            fd = os.open(os.path.join(self.d, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, ("%d\n" % pid).encode())
            os.close(fd)
        self.state["state"] = STATE_STARTING
        self.state["started_at"] = now()
        self.state["result_seen"] = False
        self.state["completion_seen"] = False
        self.state["executor_ctx_used"] = None
        self.log_event({"type": "driver", "event": "executor_spawn",
                        "pid": self.executor.pid, "gen": self.state.get("gen", 1),
                        "resume_sid": spec.get("resume_sid") or None})

    def feed_text(self, text):
        if self.executor is None or self.executor.stdin is None:
            return False
        msg = {"type": "user",
               "message": {"role": "user",
                           "content": [{"type": "text", "text": text}]}}
        try:
            self.executor.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
            self.executor.stdin.flush()
        except (BrokenPipeError, OSError):
            return False
        self.log_event({"type": "driver", "event": "fed", "bytes": len(text)})
        return True

    def close_stdin(self):
        try:
            if self.executor and self.executor.stdin:
                self.executor.stdin.close()
        except (OSError, ValueError):
            pass

    def reap(self, flags=0):
        if self.executor is None or self.executor_exit is not None:
            return self.executor_exit
        try:
            pid, status = os.waitpid(self.executor.pid, flags)
        except ChildProcessError:
            self.executor_exit = (None, None)
            return self.executor_exit
        except OSError as exc:
            if exc.errno == errno.EINTR:
                return None
            self.executor_exit = (None, None)
            return self.executor_exit
        if pid == 0:
            return None
        if os.WIFEXITED(status):
            self.executor_exit = (os.WEXITSTATUS(status), None)
        elif os.WIFSIGNALED(status):
            self.executor_exit = (-os.WTERMSIG(status), os.WTERMSIG(status))
        else:
            self.executor_exit = (None, None)
        return self.executor_exit

    def stop_ladder(self, grace=DEFAULT_STOP_GRACE, term_grace=DEFAULT_TERM_GRACE):
        """EOF -> grace wait -> SIGTERM -> SIGKILL. Idempotent."""
        if self.stop_ladder_started is not None:
            return
        self.stop_ladder_started = now()
        self.close_stdin()  # graceful EOF: claude finishes the turn, then exits
        deadline = now() + grace
        while now() < deadline:
            if self.reap(os.WNOHANG) is not None:
                break
            time.sleep(0.2)
        if self.reap(os.WNOHANG) is None:
            try:
                os.kill(self.executor.pid, signal.SIGTERM)
            except OSError:
                pass
            deadline = now() + term_grace
            while now() < deadline:
                if self.reap(os.WNOHANG) is not None:
                    break
                time.sleep(0.2)
        if self.reap(os.WNOHANG) is None:
            try:
                os.kill(self.executor.pid, signal.SIGKILL)
            except OSError:
                pass
            self.reap(0)

    def finalize_exit(self, reason):
        if getattr(self, "_finalized", False):
            return
        self._finalized = True
        code, sig = self.executor_exit if self.executor_exit else (None, None)
        if self.state.get("stopping") or reason == "stopped":
            # orchestrator-initiated stop: SIGTERM/SIGKILL exits (143/137) are
            # the ladder's own escalation, not crashes — record stopped.
            self.state["state"] = STATE_STOPPED
        elif self.state.get("completion_seen") and code == 0:
            self.state["state"] = STATE_DONE
        elif sig is not None:
            self.state["state"] = STATE_CRASHED
        elif code == 0:
            # clean EOF exit (stop ladder or orchestrator-initiated stop)
            self.state["state"] = STATE_STOPPED if reason == "stopped" else STATE_DONE \
                if self.state.get("completion_seen") else STATE_FAILED
            if self.state["state"] == STATE_FAILED:
                self.state["terminal_reason"] = "executor exited 0 without result+completion (%s)" % reason
        else:
            self.state["state"] = STATE_FAILED
            self.state["terminal_reason"] = "executor exited with code %s (%s)" % (code, reason)
        self.state["executor_exit_code"] = code
        self.state["executor_exit_signal"] = sig
        self.state["ended_at"] = now()
        self.log_event({"type": "driver", "event": "executor_exit",
                        "code": code, "signal": sig, "reason": reason,
                        "final_state": self.state["state"]})

    # ── event stream handling ────────────────────────────────────────────
    def poll_transcript_ctx(self):
        """ctx fallback via the suite's own context.py (transcript usage).

        Some providers (e.g. GLM-routed) zero out stream-event usage; the
        executor transcript still carries real usage records, which
        scripts/context.py already knows how to read. Stream usage keeps
        priority; this only fills in when it never produced a number.
        """
        if self.state.get("ctx_source") == "usage":
            return
        if now() - getattr(self, "_last_ctx_poll", 0.0) < 10:
            return
        self._last_ctx_poll = now()
        sid = self.state.get("sid")
        if not sid:
            return
        ctx_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "context.py")
        if not os.path.isfile(ctx_py):
            return
        try:
            out = subprocess.run(
                [sys.executable, ctx_py, "--session", sid, "--root", self.root],
                capture_output=True, text=True, timeout=30)
            data = json.loads(out.stdout.strip().splitlines()[-1])
            used = data.get("used_tokens")
            if isinstance(used, (int, float)) and used > 0:
                self.state["executor_ctx_used"] = int(used)
                self.state["ctx_source"] = "transcript"
                self.save()
        except (OSError, ValueError, subprocess.SubprocessError, IndexError):
            pass

    def poll_sessions_file(self):
        """Resolve sid/version from ~/.claude/sessions/<executor_pid>.json.

        Interactive stream-json sessions (no -p) never emit system/init —
        this per-pid file is the authoritative identity source for headless
        interactive processes (same source cc-connect and gclear use). Guard
        against pid reuse by matching the pid field inside the file.
        """
        pid = self.state.get("executor_pid")
        if not pid or self.state.get("sid"):
            return
        path = os.path.join(os.path.expanduser("~"), ".claude", "sessions", "%d.json" % pid)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if str(data.get("pid")) != str(pid):
            return
        sid = data.get("sessionId")
        if sid:
            self.state["sid"] = sid
            self.state["claude_version"] = data.get("version") or self.state.get("claude_version")
            if self.state["state"] == STATE_STARTING:
                self.state["state"] = STATE_WORKING
            self.save()

    def handle_event(self, line):
        line = line.strip()
        if not line:
            return
        try:
            payload = json.loads(line)
        except ValueError:
            payload = {"type": "unparsed", "raw": line[:2000]}
        etype = payload.get("type")
        if etype == "system" and payload.get("subtype") == "init":
            self.state["sid"] = payload.get("session_id") or self.state.get("sid")
            self.state["model"] = payload.get("model") or self.state.get("model")
            self.state["claude_version"] = payload.get("version") or self.state.get("claude_version")
            if self.state["state"] == STATE_STARTING:
                self.state["state"] = STATE_WORKING
        elif etype == "assistant":
            usage = (payload.get("message") or {}).get("usage")
            ctx = ctx_from_usage(usage)
            if ctx:
                self.state["executor_ctx_used"] = ctx
            model = (payload.get("message") or {}).get("model")
            if model and not self.state.get("model"):
                self.state["model"] = model
            if self.state["state"] in (STATE_STARTING, STATE_IDLE):
                self.state["state"] = STATE_WORKING
        elif etype == "result":
            self.state["result_seen"] = True
            self.state["last_result_ts"] = now()
            if completion_present(self.d):
                self.state["completion_seen"] = True
                self.state["state"] = STATE_DONE
            elif self.state["state"] != STATE_BLOCKED:
                self.state["state"] = STATE_IDLE
        self.log_event(payload)

    def maybe_flip_done(self):
        if self.state.get("result_seen") and not self.state.get("completion_seen") \
                and now() - self.last_completion_check > COMPLETION_POLL:
            self.last_completion_check = now()
            if completion_present(self.d):
                self.state["completion_seen"] = True
                if self.state["state"] in (STATE_IDLE, STATE_WORKING):
                    self.state["state"] = STATE_DONE
                    self.save()

    # ── main loop ────────────────────────────────────────────────────────
    def run(self):
        signal.signal(signal.SIGTERM, self._on_sigterm)
        signal.signal(signal.SIGINT, self._on_sigterm)
        self.load()
        fifo_fd = os.open(self.fifo_path, os.O_RDWR | os.O_NONBLOCK)
        buf = b""
        fifo_buf = b""
        try:
            self.spawn_executor()
            while True:
                if self.executor_exit is None:
                    self.reap(os.WNOHANG)
                    self.poll_sessions_file()
                    self.poll_transcript_ctx()
                watched = [fifo_fd]
                if self.executor_exit is None and self.executor.stdout is not None:
                    watched.append(self.executor.stdout.fileno())
                timeout = 0.5 if self.executor_exit is None else 1.0
                try:
                    ready, _, _ = select.select(watched, [], [], timeout)
                except InterruptedError:
                    ready = []
                if self.executor_exit is None and self.executor.stdout is not None \
                        and self.executor.stdout.fileno() in ready:
                    chunk = os.read(self.executor.stdout.fileno(), 65536)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            try:
                                self.handle_event(line.decode("utf-8", "replace"))
                            except (OSError, RuntimeError) as exc:
                                self.state["pump_errors"] = self.state.get("pump_errors", 0) + 1
                                self.log_event({"type": "driver", "event": "pump_error",
                                                "error": str(exc)[:500]})
                    else:
                        # EOF on executor stdout: process is gone (or closed).
                        self.reap(0)
                if fifo_fd in ready:
                    try:
                        chunk = os.read(fifo_fd, 65536)
                    except BlockingIOError:
                        chunk = b""
                    if chunk:
                        fifo_buf += chunk
                        while b"\n" in fifo_buf:
                            line, fifo_buf = fifo_buf.split(b"\n", 1)
                            self._on_fifo_line(line)
                self.maybe_flip_done()
                if self.executor_exit is not None:
                    reason = "stopped" if self.state.get("stopping") else "exited"
                    self.finalize_exit(reason)
                    break
        finally:
            try:
                os.close(fifo_fd)
            except OSError:
                pass
            if self.executor_exit is None:
                # pump is dying unexpectedly (uncaught error): tear down the
                # executor via the ladder so we never leave a silent orphan.
                self.state["stopping"] = True
                try:
                    self.stop_ladder()
                except Exception:
                    pass
                self.finalize_exit("pump-exit")

    def _on_fifo_line(self, raw):
        try:
            cmd = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            cmd = {"cmd": "raw", "text": raw.decode("utf-8", "replace")}
        action = cmd.get("cmd")
        if action == "feed":
            ok = self.feed_text(cmd.get("text", ""))
            if not ok:
                self.log_event({"type": "driver", "event": "feed_failed"})
        elif action == "nop":
            self.log_event({"type": "driver", "event": "ping"})
        else:
            self.log_event({"type": "driver", "event": "unknown_cmd",
                            "cmd": str(action)[:80]})

    def _on_sigterm(self, signum, _frame):
        # Graceful stop: record intent; the main loop tears down via ladder.
        self.state["stopping"] = True
        if self.executor_exit is None:
            try:
                self.stop_ladder()
            except Exception:
                pass
            self.finalize_exit("stopped")
        raise SystemExit(0)


def pump_main(args):
    d = qgo_dir(args.root, args.id)
    try:
        pump = Pump(args.id, args.root)
        pump.run()
    except (RuntimeError, OSError) as exc:
        # Last-resort: make the failure visible in state.json, never silent.
        st = read_state(os.path.join(d, "state.json")) or {}
        st["state"] = STATE_CRASHED
        st["terminal_reason"] = "pump crashed: %s" % exc
        try:
            write_state(os.path.join(d, "state.json"), st)
        except OSError:
            pass
        raise


# ──────────────────────────── CLI subcommands ────────────────────────────

def prepare_dir(root, exec_id):
    d = qgo_dir(root, exec_id)
    os.makedirs(d, exist_ok=True)
    os.chmod(os.path.join(root, "tmp", "qgo"), 0o700)
    os.chmod(d, 0o700)
    fifo = os.path.join(d, "stdin.fifo")
    if not os.path.exists(fifo):
        os.mkfifo(fifo, 0o600)
    os.makedirs(os.path.join(d, "completion"), exist_ok=True)
    return d


def spawn_pump(exec_id, root):
    log_path = os.path.join(qgo_dir(root, exec_id), "pump.log")
    log = open(log_path, "ab", 0)
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "_pump", "--id", exec_id, "--root", root],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        start_new_session=True,
    )
    log.close()
    return proc


def active_or_none(state, d):
    """Return (pump_alive, executor_alive, unknown_reason).

    Each boolean is True/False, or None when liveness could not be determined.
    ``unknown_reason`` is a short diagnostic in that case. Callers MUST fail
    closed on unknowns instead of treating them as "dead" (the old fail-open
    behaviour caused duplicate spawns and false "stopped" reports).
    """
    unknown = None
    try:
        pump_alive = proc_compat.alive(state.get("pump_pid"), "executor_driver.py")
    except proc_compat.Unavailable as exc:
        pump_alive = None
        unknown = "pump liveness unknown: %s" % exc
    try:
        exec_alive = proc_compat.alive(state.get("executor_pid"), "claude")
    except proc_compat.Unavailable as exc:
        exec_alive = None
        unknown = unknown or ("executor liveness unknown: %s" % exc)
    return pump_alive, exec_alive, unknown


def wait_for_init(d, timeout):
    deadline = now() + timeout
    while now() < deadline:
        st = read_state(os.path.join(d, "state.json"))
        if st and st.get("sid") and st.get("state") not in (STATE_STARTING,):
            return st, None
        if st and st.get("state") in TERMINAL_STATES:
            return st, "executor reached terminal state %s before init" % st["state"]
        time.sleep(0.5)
    return read_state(os.path.join(d, "state.json")), "init event not seen within %ss" % timeout


def cmd_start(args):
    check_id(args.id)
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        fail("root does not exist: %s" % root)
    mode = validate_mode(args)
    require_claude_on_path()
    d = prepare_dir(root, args.id)
    state_path = os.path.join(d, "state.json")
    prev = read_state(state_path)
    if prev and prev.get("state") not in TERMINAL_STATES:
        pump_alive, _, unknown = active_or_none(prev, d)
        if unknown:
            fail("cannot determine whether the previous run is alive (%s); "
                 "refusing to spawn a duplicate" % unknown, 3)
        if pump_alive:
            # idempotent: an active run is returned as-is, never double-spawned
            st = {"ok": True, "idempotent": True, "id": args.id,
                  "state": prev.get("state"), "sid": prev.get("sid"),
                  "model": prev.get("model"), "note": "executor already active; nothing spawned"}
            emit(st)
            return
        # stale non-terminal state without a living pump: reclaim as fresh gen
    gen = ((prev or {}).get("gen") or 0) + 1
    if prev and prev.get("gen"):
        # previous generation evidence stays in events.log (gen markers)
        pass
    mcp_path = os.path.join(d, "mcp.json")
    n_total, n_kept = filter_mcp(root, mcp_path)
    spec = {
        "id": args.id,
        "root": root,
        "permission_mode": mode,
        "allowed_tools": args.allowed_tools or "",
        "mcp_path": mcp_path,
        "mcp_servers_total": n_total,
        "mcp_servers_kept": n_kept,
        "resume_sid": None,  # only the `resume` subcommand re-enters a session
        "gen": gen,
        "created_at": now(),
    }
    fd = os.open(os.path.join(d, "spec.json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=1)
    state = {
        "id": args.id, "root": root, "gen": gen, "state": STATE_STARTING,
        "sid": None, "model": None, "claude_version": None,
        "executor_pid": None, "pump_pid": None,
        "result_seen": False, "completion_seen": False,
        "executor_ctx_used": None,
        "permission_mode": mode,
    }
    write_state(state_path, state)
    pump = spawn_pump(args.id, root)
    state["pump_pid"] = pump.pid
    write_state(state_path, state)
    st, err = wait_for_init(d, args.start_timeout)
    if err or not st or not st.get("sid"):
        # fail-closed: tear down whatever half-started. This pump is OUR spawn,
        # so PID reuse is impossible — terminate it directly rather than relying
        # on `ps` (teardown must not be skipped just because ps is unavailable).
        try:
            pump.terminate()
        except OSError:
            pass
        deadline = now() + DEFAULT_STOP_GRACE + DEFAULT_TERM_GRACE + 5
        while pump.poll() is None and now() < deadline:
            time.sleep(0.5)
        if pump.poll() is None:
            try:
                pump.kill()
            except OSError:
                pass
            try:
                pump.wait(timeout=5)
            except subprocess.SubprocessError:
                pass
        st = read_state(state_path) or {}
        emit({"ok": False, "error": err or "no init event", "id": args.id,
              "state": st.get("state"), "mcp_filtered": "%d/%d" % (n_kept, n_total)})
        raise SystemExit(3)
    emit({"ok": True, "id": args.id, "sid": st.get("sid"), "model": st.get("model"),
          "version": st.get("claude_version"), "state": st.get("state"),
          "executor_pid": st.get("executor_pid"), "gen": gen,
          "mcp_filtered": "%d/%d" % (n_kept, n_total)})


def cmd_resume(args):
    check_id(args.id)
    root = os.path.abspath(args.root)
    d = qgo_dir(root, args.id)
    state_path = os.path.join(d, "state.json")
    prev = read_state(state_path)
    if not prev:
        fail("no state.json for %s under %s (resume needs an existing run)" % (args.id, d))
    if prev.get("state") not in TERMINAL_STATES:
        pump_alive, _, unknown = active_or_none(prev, d)
        if unknown:
            fail("cannot determine whether the executor is alive (%s); "
                 "refusing to resume" % unknown, 3)
        if pump_alive:
            emit({"ok": False, "error": "executor still active; use feed or stop first",
                  "id": args.id, "state": prev.get("state")})
            raise SystemExit(2)
    sid = args.sid or prev.get("sid")
    if not sid:
        fail("no session id to resume (state.json has no sid; pass --sid)")
    # resume = same session, new process generation
    gen = (prev.get("gen") or 0) + 1
    spec = read_state(os.path.join(d, "spec.json")) or {}
    spec["resume_sid"] = sid
    spec["gen"] = gen
    fd = os.open(os.path.join(d, "spec.json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=1)
    if not os.path.isfile(spec.get("mcp_path") or ""):
        filter_mcp(root, os.path.join(d, "mcp.json"))
    state = {
        "id": args.id, "root": root, "gen": gen, "state": STATE_STARTING,
        "sid": sid, "model": prev.get("model"), "claude_version": prev.get("claude_version"),
        "executor_pid": None, "pump_pid": None,
        "result_seen": False, "completion_seen": False,
        "executor_ctx_used": None,
        "permission_mode": spec.get("permission_mode", "bypassPermissions"),
        "resumed_from_sid": sid,
    }
    write_state(state_path, state)
    pump = spawn_pump(args.id, root)
    state["pump_pid"] = pump.pid
    write_state(state_path, state)
    st, err = wait_for_init(d, args.start_timeout)
    if err or not st or not st.get("sid"):
        # Resume-mode reality: init emission after `claude --resume` is
        # inconsistent, and a resumed process may stay silent on stdout until
        # its first fed message. If the process is alive and the event channel
        # is live (any system event logged), report readiness pending instead
        # of failing — the next feed activates it (verified in real-device T7).
        st2 = read_state(state_path) or {}
        try:
            resumed_alive = proc_compat.alive(st2.get("executor_pid"), "claude")
        except proc_compat.Unavailable as exc:
            fail("cannot determine executor liveness (%s); refusing to report "
                 "readiness" % exc, 3)
        if resumed_alive:
            emit({"ok": True, "resumed": True, "id": args.id, "sid": sid,
                  "model": st2.get("model"), "version": st2.get("claude_version"),
                  "state": st2.get("state"), "gen": gen,
                  "readiness": "pending-first-message",
                  "note": "no init/assistant event within window but process alive; next feed activates"})
            return
        emit({"ok": False, "error": err or "no init event on resume", "id": args.id,
              "sid": sid, "state": (st or {}).get("state")})
        raise SystemExit(3)
    emit({"ok": True, "resumed": True, "id": args.id, "sid": st.get("sid"),
          "model": st.get("model"), "version": st.get("claude_version"),
          "state": st.get("state"), "gen": gen})


def status_payload(d, exec_id):
    state_path = os.path.join(d, "state.json")
    st = read_state(state_path)
    if not st:
        return {"ok": False, "error": "no state.json", "id": exec_id}
    payload = dict(st)
    payload["ok"] = True
    t = now()
    last_ts = st.get("last_event_ts") or st.get("started_at")
    payload["last_event_age_seconds"] = round(t - last_ts, 1) if last_ts else None
    pump_alive, exec_alive, unknown = active_or_none(st, d)
    payload["pump_alive"] = pump_alive
    payload["executor_alive"] = exec_alive
    if unknown:
        # Cannot judge liveness: read-only report, never publish a terminal
        # state (a wrong "crashed" would corrupt the orchestrator's view).
        payload["process_state"] = "unknown"
        payload["unknown_reason"] = unknown
        return payload
    payload["process_state"] = "alive" if (pump_alive or exec_alive) else "dead"
    if not exec_alive and st.get("state") not in TERMINAL_STATES:
        # bookkeeping correction only (plan §3.1): never live takeover
        st["state"] = STATE_CRASHED
        st.setdefault("terminal_reason", "executor pid gone without terminal state (bookkeeping fix)")
        write_state(state_path, st)
        payload["state"] = st["state"]
        payload["bookkeeping_fix"] = True
    return payload


def cmd_status(args):
    check_id(args.id)
    root = os.path.abspath(args.root)
    emit(status_payload(qgo_dir(root, args.id), args.id))


def cmd_feed(args):
    check_id(args.id)
    root = os.path.abspath(args.root)
    d = qgo_dir(root, args.id)
    st = read_state(os.path.join(d, "state.json"))
    if not st:
        fail("no state.json for %s" % args.id)
    pump_alive, exec_alive, unknown = active_or_none(st, d)
    if unknown:
        fail("cannot determine whether the executor is alive (%s); refusing "
             "to feed" % unknown, 3)
    if not exec_alive:
        fail("executor not alive (state=%s); start or resume first" % st.get("state"))
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    if not text or not text.strip():
        fail("empty feed text")
    frame = json.dumps({"cmd": "feed", "text": text, "ts": now()},
                       ensure_ascii=False)
    try:
        # non-blocking open: fail fast when no pump reader is holding the fifo
        fd = os.open(os.path.join(d, "stdin.fifo"), os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        fail("no pump reader on stdin.fifo (pump dead or run terminal); start/resume first")
    try:
        os.set_blocking(fd, True)  # blocking write: large frames drain safely (single writer)
        os.write(fd, frame.encode("utf-8") + b"\n")
    finally:
        os.close(fd)
    emit({"ok": True, "id": args.id, "fed_bytes": len(text),
          "note": "verify adoption via status (last_event=user/assistant) or events.log"})


def cmd_stop(args):
    check_id(args.id)
    root = os.path.abspath(args.root)
    d = qgo_dir(root, args.id)
    state_path = os.path.join(d, "state.json")
    st = read_state(state_path)
    if not st:
        fail("no state.json for %s" % args.id)
    pump_alive, exec_alive, unknown = active_or_none(st, d)
    if unknown:
        fail("cannot determine process liveness (%s); refusing to stop or "
             "normalise state" % unknown, 3)
    if not pump_alive and not exec_alive:
        if st.get("state") not in TERMINAL_STATES:
            st["state"] = STATE_STOPPED
            st.setdefault("terminal_reason", "stop: no live process found")
            write_state(state_path, st)
        emit({"ok": True, "id": args.id, "state": st.get("state"),
              "note": "nothing live; state normalized"})
        return
    if pump_alive:
        try:
            os.kill(st["pump_pid"], signal.SIGTERM)  # pump runs the EOF ladder
        except OSError as exc:
            fail("cannot signal pump %s: %s" % (st["pump_pid"], exc))
    else:
        # pump dead, executor alive: run the ladder here (stop+resume doctrine)
        try:
            os.kill(st["executor_pid"], signal.SIGTERM)
        except OSError:
            pass
    deadline = now() + args.grace + DEFAULT_TERM_GRACE + 10
    while now() < deadline:
        st = read_state(state_path) or {}
        if st.get("state") in TERMINAL_STATES:
            break
        time.sleep(0.5)
    st = read_state(state_path) or {}
    if st.get("state") not in TERMINAL_STATES:
        emit({"ok": False, "error": "stop did not reach a terminal state in time",
              "id": args.id, "state": st.get("state"),
              "hint": "re-run stop; escalation ladder continues"})
        raise SystemExit(3)
    emit({"ok": True, "id": args.id, "state": st.get("state"),
          "exit_code": st.get("executor_exit_code"), "sid": st.get("sid")})


def cmd_sweep(args):
    root = os.path.abspath(args.root)
    base = os.path.join(root, "tmp", "qgo")
    report = []
    if not os.path.isdir(base):
        emit({"ok": True, "scanned": 0, "actions": report})
        return
    for exec_id in sorted(os.listdir(base)):
        d = os.path.join(base, exec_id)
        state_path = os.path.join(d, "state.json")
        st = read_state(state_path)
        if st is None:
            continue
        entry = {"id": exec_id, "state_before": st.get("state")}
        pump_alive, exec_alive, unknown = active_or_none(st, d)
        if unknown:
            # Cannot prove liveness: record the fact, never touch state or signal.
            entry["action"] = "unknown"
            entry["unknown_reason"] = unknown
            entry["state_after"] = st.get("state")
            report.append(entry)
            continue
        if st.get("state") in TERMINAL_STATES and not exec_alive and not pump_alive:
            entry["action"] = "none (terminal)"
        elif exec_alive and not pump_alive:
            # orphan main path: graceful stop here, orchestrator resumes later
            try:
                os.kill(st["executor_pid"], signal.SIGTERM)
            except OSError:
                pass
            deadline = now() + DEFAULT_TERM_GRACE
            try:
                while proc_alive(st["executor_pid"], "claude") and now() < deadline:
                    time.sleep(0.3)
                still_alive = proc_alive(st["executor_pid"], "claude")
            except proc_compat.Unavailable as exc:
                # Escalation target cannot be confirmed: do not escalate.
                entry["action"] = "unknown"
                entry["unknown_reason"] = "liveness unavailable during stop ladder: %s" % exc
                entry["state_after"] = read_state(state_path).get("state")
                report.append(entry)
                continue
            if still_alive:
                try:
                    os.kill(st["executor_pid"], signal.SIGKILL)
                except OSError:
                    pass
            st["state"] = STATE_ORPHANED
            st["terminal_reason"] = "sweep: executor alive, pump dead -> stopped; resume to continue"
            write_state(state_path, st)
            entry["action"] = "orphan-stop"
        elif not exec_alive and not pump_alive and st.get("state") not in TERMINAL_STATES:
            st["state"] = STATE_CRASHED
            st.setdefault("terminal_reason", "sweep: both processes dead without terminal state (bookkeeping fix)")
            write_state(state_path, st)
            entry["action"] = "bookkeeping-fix"
        elif pump_alive:
            entry["action"] = "none (pump alive; managed run)"
        else:
            entry["action"] = "none"
        entry["state_after"] = read_state(state_path).get("state")
        report.append(entry)
    emit({"ok": True, "scanned": len(report), "actions": report})


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="executor_driver.py",
        description="qgo D-backend executor driver (stream-json pipe carrier)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("start", help="spawn pump+executor, wait for init event")
    p.add_argument("--id", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--permission-mode", default="bypassPermissions",
                   choices=list(ALLOWED_MODES) + ["default", "acceptEdits", "plan"])
    p.add_argument("--allowed-tools", default="")
    p.add_argument("--start-timeout", type=int, default=DEFAULT_START_TIMEOUT)
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("resume", help="resume a dead run in the same session (--resume <sid>)")
    p.add_argument("--id", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--sid", default="")
    p.add_argument("--start-timeout", type=int, default=DEFAULT_START_TIMEOUT)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("status", help="one-shot JSON status snapshot")
    p.add_argument("--id", required=True)
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("feed", help="write one framed user message into the executor")
    p.add_argument("--id", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--text", default="")
    p.add_argument("--file", default="")
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("stop", help="graceful stop (EOF -> TERM -> KILL ladder)")
    p.add_argument("--id", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--grace", type=int, default=DEFAULT_STOP_GRACE)
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("sweep", help="scan tmp/qgo/* for orphans; stop+mark, never takeover")
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("_pump", help=argparse.SUPPRESS)
    p.add_argument("--id", required=True)
    p.add_argument("--root", required=True)
    p.set_defaults(func=pump_main)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        raise SystemExit(2)
    args.func(args)


if __name__ == "__main__":
    main()
