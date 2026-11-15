#!/usr/bin/env python3
"""gclear 1.2.4 evidence engine.

Pure stdlib, self-contained: no dependency on xcontext internals. Every
command is project-scoped and fails closed when the evidence is ambiguous.

CLI:
  run --timeout <sec> -- <command...>     portable timeout wrapper
  path --path <p> [--relative-to <dir>]   canonicalize / relativize a path
  budget <root> <deadline> [operation]
  receipt <root> <deadline> <operation> <phase> <sid> <stamp> <clear> <inject>
  marker <root> <sid> <marker> <since>
  idle <root> <sid>                 -> idle | busy
  idle-detail <root> <sid>          -> {"state": ..., "detail": ...}
  discover <root> <old_sid> <since> -> ok <sid> | ambiguous <a>,<b> | none
  has-clear <root> <sid> <since>    -> 1 | 0
  caller <root>                     -> cc <sid> <pid> | cli <sid> <pid>
  pane <root> <pane>                -> <sid>\t<used>
  pane-model <root> <pane>          -> model
"""
import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import math
import time
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import proc_compat  # noqa: E402  (local stdlib-only helper)


# ── transcript ownership / marker evidence ────────────────────────────────
def row_owned(row, root, sid, since):
    """One attribution/time boundary for marker, Read and tool result rows."""
    try:
        if (not isinstance(row, dict) or row.get('sessionId') != sid
                or any(row.get(k) for k in ('isMeta', 'isSidechain', 'isCompactSummary'))
                or not isinstance(row.get('cwd'), str) or not row['cwd']
                or Path(row['cwd']).resolve() != Path(root).resolve()):
            return False
        stamp = datetime.datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        return (stamp.tzinfo is not None and stamp.utcoffset() is not None
                and math.isfinite(float(since)) and stamp.timestamp() >= float(since))
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return False


def user_marker(row, marker):
    msg = row.get('message')
    if not isinstance(msg, dict) or row.get('type') != 'user' or msg.get('role') != 'user':
        return False
    content = msg.get('content')
    if isinstance(content, str):
        return bool(marker) and marker in content
    return (isinstance(content, list) and bool(marker)
            and all(isinstance(b, dict) and b.get('type') == 'text'
                    and isinstance(b.get('text'), str) for b in content)
            and any(marker in b['text'] for b in content))


# ── operation journal / receipt / budget ──────────────────────────────────
def operation_path(root, value):
    path = Path(value).absolute()
    base = Path(root).resolve() / '.claude/skills/gclear/.operations'
    if (path.parent.resolve() != base
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.json', path.name)
            or path.name.endswith('.receipt.json')
            or path.is_symlink() or path.resolve() != path):
        raise ValueError('Invalid operation boundary')
    return path


def atomic_save(path, value):
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream)
        if value.get('status') == 'succeeded' and time.time() >= value['deadline']:
            raise TimeoutError('Success publication expired')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def operation_budget(root, deadline, operation=''):
    """Return remaining seconds.

    Supervised runs carry an operation journal written by an external
    supervisor: the externally supplied deadline is exact and must stay <=900s
    and must never be rewritten by gclear. Non-supervised runs get a larger
    internal allowance.
    """
    deadline = float(deadline)
    if not math.isfinite(deadline):
        raise ValueError('Invalid deadline')
    cap = 900.0
    if operation:
        path = operation_path(root, operation)
        record = json.loads(path.read_text())
        if (record.get('operation') != str(path) or record.get('root') != str(Path(root).resolve())
                or record.get('status') not in ('armed', 'observing')
                or record.get('deadline') != deadline
                or not 0 < deadline - record['started'] <= 900):
            raise ValueError('Operation is not active')
    else:
        cap = 1500.0
    remaining = deadline - time.time()
    if not 0 < remaining <= cap:
        raise ValueError('Operation expired')
    return remaining


def helper_receipt(root, deadline, operation, phase, sid, stamp, clear, inject):
    operation_budget(root, deadline, operation)
    if not operation:
        return
    path = operation_path(root, operation)
    if phase == 'clear' and json.loads(path.read_text()).get('old_sid') != sid:
        raise ValueError('Unexpected old SID')
    receipt = path.with_name(path.name + '.receipt.json')
    if receipt.is_symlink():
        raise ValueError('Invalid receipt boundary')
    atomic_save(receipt, dict(operation=str(path), root=str(Path(root).resolve()),
                             deadline=float(deadline), phase=phase, expectedSID=sid,
                             submit_ts=float(stamp) if stamp else None,
                             clear_receipt=clear, inject_receipt=inject))


def committed(root, sid, marker, since):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        return False
    path = transcript_path(root, sid)
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row_owned(row, root, sid, since) and user_marker(row, marker):
                        return True
                except (ValueError, TypeError, KeyError, AttributeError, OSError):
                    continue
    except OSError:
        pass
    return False


# ── transcript helpers ────────────────────────────────────────────────────
def projects_dir(root):
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path(root).resolve()))


def transcript_path(root, sid):
    return projects_dir(root) / (sid + ".jsonl")


def _rows(path):
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _timestamp(row):
    value = row.get('timestamp')
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.timestamp()


def _texts(content):
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get('text') for b in content
                if isinstance(b, dict) and b.get('type') == 'text'
                and isinstance(b.get('text'), str)]
    return []


def _has_block(content, kind):
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get('type') == kind for b in content)


IGNORE_TYPES = {'attachment', 'permission-mode', 'last-prompt',
                'file-history-snapshot', 'summary'}
IGNORE_TEXT = ('<command-name>', '<task-notification>', '<local-command-caveat>')


def idle_scan(root, sid):
    """Return ``(state, detail)`` from transcript semantics.

    `message.stop_reason` is the single primary signal. Abort markers are
    checked before stop_reason. daemon.log is deliberately NOT consulted here:
    a long in-flight turn may emit no new INFO line, so the last `turn
    complete` cannot prove idleness.

    Queue accounting is WINDOW-scoped, not transcript-lifetime: `end_turn`
    consumes every enqueue seen before it, so only messages that arrive after
    the last completed turn keep the session busy. cc-connect steers busy
    messages into a running turn with a bare `enqueue` and never writes the
    matching `dequeue`, so a lifetime counter would latch to busy forever.
    """
    state = 'idle'
    queue = 0
    last_end_turn = None
    enqueue_ts = []
    for row in _rows(transcript_path(root, sid)):
        if row.get('isApiErrorMessage') is True:
            state = 'idle'
            continue
        if row.get('type') == 'system':
            subtype = row.get('subtype')
            if subtype in ('error', 'api_error') or row.get('isApiErrorMessage') is True:
                state = 'idle'
            continue
        if row.get('isMeta') or row.get('isSidechain') or row.get('isCompactSummary'):
            continue
        kind = row.get('type')
        if kind == 'queue-operation':
            operation = row.get('operation')
            if operation == 'enqueue':
                queue += 1
                enqueue_ts = (enqueue_ts + [_timestamp(row)])[-5:]
            elif operation in ('dequeue', 'remove'):
                queue = max(0, queue - 1)
            continue
        if kind in IGNORE_TYPES:
            continue
        message = row.get('message')
        if not isinstance(message, dict):
            continue
        content = message.get('content')
        texts = _texts(content)
        if texts and all(any(mark in text for mark in IGNORE_TEXT) for text in texts):
            continue
        role = message.get('role')
        if kind == 'assistant' or role == 'assistant':
            stop_reason = message.get('stop_reason')
            if stop_reason == 'end_turn':
                state = 'idle'
                # A completed turn has consumed every pending steer/enqueue.
                queue = 0
                enqueue_ts = []
                last_end_turn = _timestamp(row)
            elif stop_reason:
                state = 'busy'
            elif _has_block(content, 'tool_use'):
                state = 'busy'
            else:
                # No stop_reason at all: text/thinking-only rows are treated
                # conservatively as busy until an end_turn proves otherwise.
                state = 'busy'
        elif kind == 'user' or role == 'user':
            if _has_block(content, 'tool_result'):
                state = 'busy'
            elif texts or isinstance(content, str):
                state = 'busy'
    if queue > 0:
        state = 'busy'
    detail = {'queue': queue, 'last_end_turn': last_end_turn,
              'enqueue_ts': enqueue_ts}
    return state, detail


def idle_state(root, sid):
    return idle_scan(root, sid)[0]


def has_clear_command(root, sid, since):
    for row in _rows(transcript_path(root, sid)):
        kind = row.get('type')
        if kind != 'user':
            continue
        content = (row.get('message') or {}).get('content') if isinstance(row.get('message'), dict) else None
        blob = content if isinstance(content, str) else '\n'.join(_texts(content))
        if not isinstance(blob, str) or '<command-name>/clear</command-name>' not in blob:
            continue
        stamp = _timestamp(row)
        if stamp is None or stamp >= float(since) - 2:
            return True
    return False


def steered(root, sid, since):
    """True when the old session received `/clear` as plain user text.

    That is the queue/steer failure mode: the command was consumed as an
    ordinary prompt instead of clearing. It never creates a new session.
    """
    since = float(since)
    for row in _rows(transcript_path(root, sid)):
        if row.get('type') != 'user' or row.get('isMeta'):
            continue
        stamp = _timestamp(row)
        if stamp is None or stamp < since:
            continue
        message = row.get('message')
        content = message.get('content') if isinstance(message, dict) else None
        blob = content if isinstance(content, str) else '\n'.join(_texts(content))
        if isinstance(blob, str) and '/clear' in blob and '<command-name>' not in blob:
            return True
    return False


def discover(root, old_sid, since):
    """Find new sessions for this project created after `since`."""
    directory = projects_dir(root)
    candidates = []
    since = float(since)
    try:
        names = os.listdir(directory)
    except OSError:
        return 'none'
    for name in names:
        if not name.endswith('.jsonl'):
            continue
        sid = name[:-6]
        if sid == old_sid or not sid or sid.startswith('_'):
            continue
        path = directory / name
        first = None
        owned = False
        for row in _rows(path):
            if first is None:
                first = _timestamp(row)
            if row.get('cwd') and Path(str(row['cwd'])).resolve() == Path(root).resolve():
                owned = True
            # A transcript is small at creation; once both facts are known stop.
            if first is not None and owned:
                break
        if not owned or first is None or first < since:
            continue
        candidates.append(sid)
    candidates.sort()
    if len(candidates) == 1:
        return 'ok ' + candidates[0]
    if len(candidates) > 1:
        return 'ambiguous ' + ','.join(candidates)
    return 'none'


# ── process enumeration (portable; system `ps` via proc_compat) ──────────
def _descendants(pid):
    return proc_compat.descendants(pid)


def _claude_pids(pane_pid):
    pids = []
    for pid in [pane_pid] + _descendants(pane_pid):
        info = proc_compat.lookup(pid, fresh=False)
        if info is not None and info.comm == 'claude':
            pids.append(pid)
    return pids


def _session_record(pid):
    path = Path(os.path.expanduser('~')) / '.claude/sessions' / (str(pid) + '.json')
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _same_root(value, root):
    try:
        return bool(value) and Path(str(value)).resolve() == Path(root).resolve()
    except OSError:
        return False


def _pane_pid(pane):
    import subprocess
    result = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{pane_pid}"],
                            capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def _args_has_config(cmdline, config):
    """Space-token match, matching the bash side (`case "$args " in ...`).

    A path that itself contains spaces conservatively fails to match, which
    routes nothing rather than routing wrongly.
    """
    if not config:
        return False
    padded = ' ' + (cmdline or '').strip() + ' '
    return (' ' + config + ' ') in padded or (' --config=' + config + ' ') in padded


def _ancestor_cc_daemon(pid, config):
    for _ in range(10):
        info = proc_compat.lookup(pid)
        if info is None or info.ppid <= 1:
            return False
        parent = info.ppid
        ancestor = proc_compat.lookup(parent)
        if ancestor is not None and ancestor.comm.startswith('cc-connect'):
            if _args_has_config(proc_compat.args(parent), config):
                return True
        pid = parent
    return False


def caller(root):
    """Anchor the caller's own claude process.

    CLAUDE_CODE_SESSION_ID -> ~/.claude/sessions/<pid>.json -> the caller's
    claude pid. Only that process's ancestry decides CC vs CLI; env leakage of
    CC_* into a tmux session can never reroute the decision.
    """
    sid = os.environ.get('CLAUDE_CODE_SESSION_ID', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', sid or ''):
        return None
    home = Path(os.path.expanduser('~')) / '.claude/sessions'
    matches = []
    try:
        names = os.listdir(home)
    except OSError:
        return None
    try:
        for name in names:
            if not name.endswith('.json'):
                continue
            pid = name[:-5]
            if not pid.isdigit():
                continue
            record = _session_record(int(pid))
            if not record:
                continue
            if record.get('sessionId') != sid:
                continue
            if not _same_root(record.get('cwd'), root):
                continue
            info = proc_compat.lookup(int(pid))
            if info is None or info.comm != 'claude':
                continue
            matches.append(int(pid))
        if len(matches) != 1:
            return None
        pid = matches[0]
        config = str(Path(root).resolve() / '.cc-connect/config.toml')
        if _ancestor_cc_daemon(pid, config):
            return ('cc', sid, pid)
        pane = os.environ.get('TMUX_PANE', '')
        if pane:
            pane_pid = _pane_pid(pane)
            if pane_pid is not None and (pid == pane_pid or pid in set(_descendants(pane_pid))):
                return ('cli', sid, pid)
        return None
    except proc_compat.Unavailable:
        # Anchor cannot be proven -> caller falls back to fail-closed handling.
        return None


def pane_snapshot(root, pane):
    pane_pid = _pane_pid(pane)
    if pane_pid is None:
        return None
    candidates = set()
    for pid in _claude_pids(pane_pid):
        record = _session_record(pid)
        if record and _same_root(record.get('cwd'), root) and record.get('sessionId'):
            candidates.add(record['sessionId'])
    if len(candidates) != 1:
        return None
    sid = next(iter(candidates))
    return (sid, _used_tokens(root, sid), _model_of(root, sid))


def _used_tokens(root, sid):
    used = ''
    for row in _rows(transcript_path(root, sid)):
        message = row.get('message')
        if not isinstance(message, dict):
            continue
        usage = message.get('usage')
        if isinstance(usage, dict):
            total = usage.get('input_tokens')
            if isinstance(total, int):
                used = str(total)
    return used


def _model_of(root, sid):
    model = ''
    for row in _rows(transcript_path(root, sid)):
        message = row.get('message')
        if isinstance(message, dict) and message.get('role') == 'assistant':
            if isinstance(message.get('model'), str):
                model = message['model']
    return model


def cmd_path(argv):
    """Canonicalize a path, optionally relative to a base directory."""
    path = None
    relative_to = None
    index = 0
    while index < len(argv):
        if argv[index] == '--path' and index + 1 < len(argv):
            path = argv[index + 1]
            index += 2
        elif argv[index] == '--relative-to' and index + 1 < len(argv):
            relative_to = argv[index + 1]
            index += 2
        else:
            return 2
    if path is None:
        return 2
    resolved = Path(path).resolve(strict=False)
    if relative_to is not None:
        base = Path(relative_to).resolve(strict=False)
        print(os.path.relpath(str(resolved), str(base)))
    else:
        print(str(resolved))
    return 0


def cmd_run(argv):
    """Portable replacement for ``timeout --foreground --kill-after=1``.

    The child runs in its own session (so a terminal Ctrl-C reaches only this
    wrapper), inherits all three stdio streams and the complete environment
    (``BASH_FUNC_*`` exported shell functions must survive), and is torn down by
    group on timeout (SIGTERM then SIGKILL). Exit codes: child rc on normal
    exit, 124 on timeout, 128+n when killed by signal n.
    """
    timeout = None
    rest = list(argv)
    if len(rest) >= 2 and rest[0] == '--timeout':
        try:
            timeout = float(rest[1])
        except ValueError:
            return 2
        rest = rest[2:]
    if rest and rest[0] == '--':
        rest = rest[1:]
    if not rest:
        return 2
    return _run_supervised(rest, timeout)


def _run_supervised(command, timeout):
    try:
        child = subprocess.Popen(command, start_new_session=True)
    except OSError as error:
        print('handoff run: cannot execute %s: %s' % (command[0], error), file=sys.stderr)
        return 127
    forwarded = {'signum': None}

    def forward(signum, _frame):
        forwarded['signum'] = signum
        try:
            os.killpg(child.pid, signum)
        except OSError:
            pass

    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            previous[signum] = signal.signal(signum, forward)
        except (ValueError, OSError):
            pass
    deadline = time.monotonic() + timeout if timeout is not None else None
    timed_out = False
    code = None
    try:
        while True:
            try:
                code = child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    code = child.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    code = child.wait()
                break
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
    if forwarded['signum'] is not None:
        return 128 + forwarded['signum']
    if timed_out:
        return 124
    if code is None:
        return 1
    if code < 0:
        return 128 + (-code)
    return code


def main(argv):
    if len(argv) < 2:
        return 2
    command = argv[1]
    try:
        if command == 'path':
            return cmd_path(argv[2:])
        if command == 'run':
            return cmd_run(argv[2:])
        if command in ('budget', 'receipt'):
            if command == 'budget':
                print(operation_budget(*argv[2:]))
            else:
                helper_receipt(*argv[2:])
            return 0
        if command == 'marker':
            return 0 if committed(*argv[2:]) else 1
        if command == 'idle':
            print(idle_state(argv[2], argv[3]))
            return 0
        if command == 'idle-detail':
            state, detail = idle_scan(argv[2], argv[3])
            print(json.dumps({'state': state, 'detail': detail}, ensure_ascii=False))
            return 0
        if command == 'discover':
            print(discover(argv[2], argv[3], argv[4]))
            return 0
        if command == 'has-clear':
            return 0 if has_clear_command(argv[2], argv[3], argv[4]) else 1
        if command == 'steer':
            return 0 if steered(argv[2], argv[3], argv[4]) else 1
        if command == 'caller':
            found = caller(argv[2])
            if not found:
                return 2
            print('%s %s %s' % found)
            return 0
        if command in ('pane', 'pane-model'):
            snap = pane_snapshot(argv[2], argv[3])
            if not snap:
                return 1
            sid, used, model = snap
            if command == 'pane-model':
                if model:
                    print(model)
            else:
                print(sid + "\t" + used)
            return 0
    except Exception as error:
        print('Handoff guard stopped: ' + type(error).__name__, file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
