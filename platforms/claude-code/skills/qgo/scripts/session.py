import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import proc_compat  # noqa: E402  (local stdlib-only helper)


def label(value, limit):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:limit].rstrip("-") or "task"


def session_name(root, task, project=None, version=None):
    root = Path(root).resolve()
    project = project or root.name or "project"
    identity = json.dumps([str(root), task, project, version], ensure_ascii=False)
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
    normalized = version.lower() if version else ""
    target = "v" + label(normalized[1:] if normalized.startswith("v") else normalized, 24) if version else "infra-" + label(task, 24)
    return f"{label(project, 32)}-qexec-{target}-{suffix}"


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return parsed


def is_submission(record, sid, marker, since, expected_text=None):
    if (
        record.get("type") != "user"
        or record.get("sessionId") != sid
        or any(record.get(key) for key in ("isMeta", "isSidechain", "isCompactSummary", "isReplay"))
    ):
        return False
    try:
        if instant(record.get("timestamp")) <= instant(since):
            return False
    except (AttributeError, TypeError, ValueError):
        return False
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    content = message.get("content", "")
    if isinstance(content, list):
        if not all(isinstance(block, dict) and block.get("type") == "text" for block in content):
            return False
        content = "\n".join(block.get("text", "") for block in content)
    if not isinstance(content, str) or not marker or marker not in content:
        return False
    return expected_text is None or content.strip() == expected_text.strip()


def find_submission(path, sid, marker, since, expected_text=None, offset=0):
    if path.name != sid + ".jsonl":
        raise ValueError("Transcript filename must match exact SID")
    if not path.exists():
        return None
    with path.open("rb") as stream:
        stream.seek(offset)
        for line in stream:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(record, dict) and is_submission(record, sid, marker, since, expected_text):
                return {"uuid": record.get("uuid"), "timestamp": record["timestamp"], "sid": sid}
    return None


def check_identity(expected, actual):
    for field in ("session", "pane", "pid", "root", "sid", "proc_start"):
        if expected[field] != actual[field]:
            raise ValueError(f"Executor identity mismatch: {field}")


def check_official(record, pid, root, proc_start):
    if (
        record.get("pid") != pid
        or not isinstance(record.get("cwd"), str)
        or not record["cwd"]
        or Path(record["cwd"]).resolve() != root.resolve()
        or not proc_compat.start_matches(record.get("procStart"), proc_start,
                                         record.get("startedAt"))
        or record.get("kind") != "interactive"
        or record.get("entrypoint") not in ("cli", "sdk-cli")
    ):
        raise ValueError("Official session identity mismatch")
    import uuid
    uuid.UUID(record.get("sessionId", ""))
    return record["sessionId"]


def transcript_for(config_root, sid):
    matches = sorted((config_root / "projects").glob("*/" + sid + ".jsonl"))
    if len(matches) == 1:
        return str(matches[0].resolve()), None
    if not matches:
        return None, "transcript_not_found"
    return None, "transcript_ambiguous"


def sid_owners(sid, config_root=None):
    config_root = Path(config_root or os.environ.get("CLAUDE_CONFIG_DIR")
                       or (Path.home() / ".claude")).resolve()
    owners = []
    for path in sorted((config_root / "sessions").glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict) or record.get("sessionId") != sid:
            continue
        pid = record.get("pid")
        expected = str(record.get("procStart", ""))
        actual = proc_compat.start_token(pid)
        live = proc_compat.start_matches(expected, actual, record.get("startedAt"))
        owners.append({"pid": pid, "proc_start": expected, "actual_proc_start": actual,
                       "live": live, "cwd": record.get("cwd"),
                       "kind": record.get("kind"), "entrypoint": record.get("entrypoint")})
    return {"sid": sid, "config_root": str(config_root), "owners": owners,
            "live_owners": sum(1 for owner in owners if owner["live"])}


BLOCKED = ("esc to interrupt", "background work is running", "exit anyway",
           "do you want to", "allow once", "allow always")
FOOTER_LINE = re.compile(r"[A-Za-z0-9][\w.\-\[\]/]*(?:\s+\d+%)?(?:\s*·.*)?")
STATUS_LINE = re.compile(r"[⏵▶▸]*\s*(?:accept edits on|bypass permissions on)(?:\s+\([^\n]*\))?(?:\s*·.*)?")


def normalized_model(value):
    base = re.sub(r"\[[^\]]*\]", "", value.strip().lower())
    return base.strip()


def model_anchor_matches(line, anchor):
    base = re.sub(r"\s+\d+(?:\.\d+)?%.*$", "", line.strip().lower())
    base = re.sub(r"\s*·.*$", "", base).strip()
    return normalized_model(base) == anchor


def pane_idle(text, model, status=None):
    tail = text.splitlines()
    while tail and not tail[-1].strip():
        tail.pop()
    tail = tail[-16:]
    joined = "\n".join(tail).lower()
    for marker in BLOCKED:
        if marker in joined:
            return False, "busy_marker:" + marker
    if status in ("busy", "waiting"):
        return False, "official_status:" + status
    prompts = [index for index, line in enumerate(tail) if line.lstrip().startswith("❯")]
    if not prompts:
        return False, "prompt_not_found"
    index = prompts[-1]
    if tail[index].lstrip()[1:].strip():
        return False, "prompt_has_draft"
    anchor = normalized_model(model)
    anchor_seen = False
    generic_seen = False
    for line in tail[index + 1:]:
        stripped = line.strip()
        if not stripped or re.fullmatch(r"[─━\-]+", stripped):
            continue
        if STATUS_LINE.fullmatch(stripped) or stripped == "? for shortcuts":
            continue
        if stripped.lower().startswith("tmux focus-events off"):
            continue
        if re.fullmatch(r"Found \d+ settings? issues?(?: · .*)?", stripped, re.I):
            continue
        if stripped[0] in "·⏵⏸" or stripped.startswith("⏵⏵"):
            continue
        if anchor and model_anchor_matches(stripped, anchor) and not anchor_seen:
            anchor_seen = True
            continue
        if not generic_seen and FOOTER_LINE.fullmatch(stripped):
            generic_seen = True
            continue
        return False, "unknown_line"
    return True, "idle"


def tmux(*args, socket=None):
    command = ["tmux"]
    if socket:
        command.extend(["-S", str(socket)])
    environment = dict(os.environ)
    environment.pop("TMUX", None)
    return subprocess.check_output([*command, *args], timeout=10, text=True, env=environment).rstrip("\n")


def target_config_root(pid):
    """Config root of the target process.

    Portable: reads the target's environ on Linux/WSL. When that is
    unreadable (macOS has no /proc) the value is inferred from this process's
    own CLAUDE_CONFIG_DIR -> ~/.claude; callers that need certainty must
    cross-check the resulting session record rather than trust the path alone.
    """
    environment = proc_compat.environ(pid)
    if environment is None:
        environment = {key: os.environ[key]
                       for key in ("CLAUDE_CONFIG_DIR", "HOME")
                       if key in os.environ}
    if environment.get("CLAUDE_CONFIG_DIR"):
        return Path(environment["CLAUDE_CONFIG_DIR"]).resolve()
    return (Path(environment.get("HOME", str(Path.home()))) / ".claude").resolve()


def pane_fields(pane, socket):
    # Some tmux clients sanitize control-character delimiters.
    fields = ("#{session_name}", "#{pane_id}", "#{pane_pid}", "#{pane_current_path}", "#{pane_dead}")
    return tuple(tmux("display-message", "-p", "-t", pane, field, socket=socket) for field in fields)


def _process_cwd(pid):
    value = proc_compat.cwd(pid)
    if value is None:
        raise ValueError("Process working directory unavailable")
    try:
        return Path(value).resolve(strict=True)
    except OSError:
        raise ValueError("Process working directory unavailable") from None


def _read_session_record(config_root, pid):
    try:
        return json.loads((config_root / "sessions" / (str(pid) + ".json")).read_text())
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise ValueError("Official session record unavailable (%s)" % type(error).__name__) from None


def identity(pane, root, socket=None):
    metadata = pane_fields(pane, socket)
    name, actual_pane, pid_text, cwd, dead = metadata
    pid = int(pid_text)
    root = root.resolve(strict=True)
    if actual_pane != pane or dead == "1" or Path(cwd).resolve() != root:
        raise ValueError("Exact live pane/root identity unavailable")
    proc_start = proc_compat.start_token(pid)
    if proc_start is None:
        raise ValueError("Process identity unavailable")
    if _process_cwd(pid) != root:
        raise ValueError("Process working directory mismatch")
    config_root = target_config_root(pid)
    record = _read_session_record(config_root, pid)
    sid = check_official(record, pid, root, proc_start)
    pane_text = tmux("capture-pane", "-p", "-t", pane, "-S", "-80", socket=socket)
    if (pane_fields(pane, socket) != metadata
            or proc_compat.start_token(pid) != proc_start
            or _process_cwd(pid) != root):
        raise ValueError("Executor identity changed during observation")
    current = _read_session_record(config_root, pid)
    if check_official(current, pid, root, proc_start) != sid:
        raise ValueError("Executor identity changed during observation")
    transcript, transcript_reason = transcript_for(config_root, sid)
    return {"session": name, "pane": pane, "pid": pid, "root": str(root),
            "sid": sid, "proc_start": proc_start, "config_root": str(config_root),
            "status": current.get("status"), "transcript": transcript,
            "transcript_reason": transcript_reason,
            "pane_sha256": hashlib.sha256(pane_text.encode()).hexdigest(), "pane_text": pane_text}


def idle_state(pane, root, socket, model):
    value = identity(pane, root, socket)
    verdict, reason = pane_idle(value["pane_text"], model, value["status"])
    return {"idle": verdict, "reason": reason, "status": value["status"]}


def main():
    parser = argparse.ArgumentParser(description="Read-only exact session identity and fresh user-submission evidence; no process control")
    commands = parser.add_subparsers(dest="command", required=True)
    name = commands.add_parser("name")
    name.add_argument("--root", type=Path, required=True)
    name.add_argument("--task", required=True)
    name.add_argument("--project", help="Actual target project name; defaults to root directory name")
    name.add_argument("--version", help="Target version for versioned tasks; omit for non-version tasks")
    who = commands.add_parser("identity")
    who.add_argument("--root", type=Path, required=True)
    who.add_argument("--pane", required=True, help="Exact tmux pane ID, such as %%12")
    who.add_argument("--expect-sid")
    who.add_argument("--socket", type=Path, help="Explicit tmux server socket")
    owners = commands.add_parser("sid-owners")
    owners.add_argument("--sid", required=True)
    owners.add_argument("--config-root", type=Path, help="Claude config root; defaults to CLAUDE_CONFIG_DIR or ~/.claude")
    idle = commands.add_parser("idle")
    idle.add_argument("--root", type=Path, required=True)
    idle.add_argument("--pane", required=True, help="Exact tmux pane ID, such as %%12")
    idle.add_argument("--socket", type=Path, help="Explicit tmux server socket")
    idle.add_argument("--model", required=True, help="Model display name configured for the target project")
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--transcript", type=Path, required=True)
    confirm.add_argument("--sid", required=True)
    confirm.add_argument("--marker", required=True)
    confirm.add_argument("--since", required=True)
    confirm.add_argument("--expected-prompt", type=Path, required=True)
    confirm.add_argument("--offset", type=int, required=True, help="Transcript byte size recorded before submitting")
    args = parser.parse_args()
    try:
        if args.command == "name":
            print(session_name(args.root, args.task, args.project, args.version))
        elif args.command == "identity":
            value = identity(args.pane, args.root, args.socket)
            if args.expect_sid and args.expect_sid != value["sid"]:
                raise ValueError("Live SID differs from expected SID; do not mutate pane")
            print(json.dumps(value, ensure_ascii=False))
        elif args.command == "sid-owners":
            print(json.dumps(sid_owners(args.sid, args.config_root), ensure_ascii=False))
        elif args.command == "idle":
            print(json.dumps(idle_state(args.pane, args.root, args.socket, args.model), ensure_ascii=False))
        else:
            instant(args.since)
            if args.offset < 0:
                raise ValueError("Offset cannot be negative")
            value = find_submission(args.transcript, args.sid, args.marker, args.since,
                                    args.expected_prompt.read_text(), args.offset)
            if value is None:
                parser.exit(1, "Exact fresh user submission not confirmed\n")
            print(json.dumps(value))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
