import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import proc_compat  # noqa: E402  (local stdlib-only helper)


DEFAULT_READ_BYTES = 4 * 1024 * 1024
INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class Unavailable(Exception):
    def __init__(self, reason, freshness="unknown"):
        self.reason = reason
        self.freshness = freshness


def sid_value(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError
    except (ValueError, AttributeError):
        raise Unavailable("invalid_session_id") from None
    return value


def positive(value):
    try:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="Read-only bounded API input snapshot; not live context usage")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--self", action="store_true", dest="self_target", help="nearest verified interactive ancestor (default)")
    target.add_argument("--pid", type=positive, help="live CLI or sdk-cli PID with official session metadata")
    target.add_argument("--tmux", metavar="PANE", help="exact pane ID; requires --socket")
    target.add_argument("--session", metavar="SID", help="explicit session snapshot; requires --transcript or --root")
    parser.add_argument("--socket", type=Path, help="explicit tmux server socket")
    parser.add_argument("--transcript", type=Path, help="exact JSONL source; no latest-session fallback")
    parser.add_argument("--root", type=Path, help="expected project root or explicit SID lookup root")
    parser.add_argument("--config-dir", type=Path, help="Claude configuration root override")
    parser.add_argument("--window-tokens", type=positive, help="verified window denominator; omitted means unknown")
    parser.add_argument("--max-read-bytes", type=positive, default=DEFAULT_READ_BYTES, help="bounded tail budget; default 4 MiB")
    parser.add_argument("--mode", choices=("snapshot", "strict"), default="snapshot", help="observed API input (default) or optional local-lineage diagnosis")
    parser.add_argument("--leaf-uuid", help="explicit local head; strict mode only")
    parser.add_argument("--label", help="caller label in the short JSON response")
    args = parser.parse_args(argv)
    if args.tmux is not None and args.socket is None:
        parser.error("--tmux requires --socket")
    if args.socket is not None and args.tmux is None:
        parser.error("--socket requires --tmux")
    if args.leaf_uuid is not None and args.mode != "strict":
        parser.error("--leaf-uuid requires --mode strict")
    return args


def read_process(pid):
    try:
        info = proc_compat.lookup(pid, fresh=True)
    except proc_compat.Unavailable:
        raise Unavailable("process_unavailable") from None
    if info is None:
        raise Unavailable("process_unavailable")
    if info.dead:
        raise Unavailable("process_not_live")
    proc_start = proc_compat.start_token(pid)
    if proc_start is None:
        raise Unavailable("process_unavailable")
    raw_cwd = proc_compat.cwd(pid)
    if raw_cwd is None:
        raise Unavailable("process_unavailable")
    try:
        resolved_cwd = str(Path(raw_cwd).resolve(strict=True))
    except (OSError, RuntimeError):
        raise Unavailable("process_unavailable") from None
    environment = {}
    target_env = proc_compat.environ(pid)
    if target_env:
        for key in ("HOME", "CLAUDE_CONFIG_DIR"):
            if key in target_env:
                environment[key] = target_env[key]
    return {"pid": pid, "parent": info.ppid, "proc_start": proc_start,
            "cwd": resolved_cwd, "environment": environment}


def config_root(environment, explicit=None):
    if explicit is not None:
        return explicit.resolve()
    if not environment.get("CLAUDE_CONFIG_DIR") and not environment.get("HOME"):
        # Q8 inference: the target's environ is unreadable (e.g. macOS). Fall
        # back to this process's own view instead of failing outright; identity
        # is still cross-checked against the official session record.
        environment = {key: os.environ[key]
                       for key in ("CLAUDE_CONFIG_DIR", "HOME") if key in os.environ}
    configured = environment.get("CLAUDE_CONFIG_DIR")
    if configured:
        path = Path(configured)
    elif environment.get("HOME"):
        path = Path(environment["HOME"]) / ".claude"
    else:
        raise Unavailable("target_config_unavailable")
    if not path.is_absolute():
        raise Unavailable("target_config_not_absolute")
    return path.resolve()


def bind_pid(pid, args):
    before = read_process(pid)
    config = config_root(before["environment"], args.config_dir)
    try:
        record = json.loads((config / "sessions" / f"{pid}.json").read_bytes())
    except FileNotFoundError:
        raise Unavailable("official_record_missing") from None
    except (OSError, ValueError, UnicodeError):
        raise Unavailable("official_record_invalid") from None
    if not isinstance(record, dict):
        raise Unavailable("official_record_invalid")
    if record.get("kind") != "interactive":
        raise Unavailable("official_not_interactive")
    cwd = Path(before["cwd"])
    raw_cwd = record.get("cwd")
    if (type(record.get("pid")) is not int or record["pid"] != pid
            or not isinstance(raw_cwd, str) or not Path(raw_cwd).is_absolute()
            or Path(raw_cwd).resolve() != cwd
            or not proc_compat.start_matches(record.get("procStart"), before["proc_start"],
                                             record.get("startedAt"))
            or record.get("kind") != "interactive"
            or record.get("entrypoint") not in ("cli", "sdk-cli")
            or (args.root is not None and args.root.resolve() != cwd)):
        raise Unavailable("official_identity_mismatch")
    sid = sid_value(record.get("sessionId"))
    if read_process(pid) != before:
        raise Unavailable("process_identity_changed")
    return {"pid": pid, "proc_start": before["proc_start"], "root": str(cwd),
            "config": str(config), "sid": sid, "process": before,
            "entrypoint": record["entrypoint"]}


def pane_metadata(args):
    if not re.fullmatch(r"%[0-9]+", args.tmux):
        raise Unavailable("exact_pane_required")
    environment = dict(os.environ)
    environment.pop("TMUX", None)
    command = ["tmux", "-S", str(args.socket), "display-message", "-p", "-t", args.tmux]
    try:
        fields = ("#{pane_id}", "#{pane_pid}", "#{pane_current_path}", "#{pane_dead}")
        values = [subprocess.run([*command, field], check=True, capture_output=True, text=True,
                                 timeout=5, env=environment).stdout.rstrip("\n") for field in fields]
        pane, pid, cwd, dead = values
        if pane != args.tmux or dead != "0" or not Path(cwd).is_absolute():
            raise ValueError
        return pane, int(pid), str(Path(cwd).resolve())
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Unavailable("pane_metadata_unavailable") from None


def descendant_pids(anchor):
    try:
        children = proc_compat.descendants(anchor)
    except proc_compat.Unavailable:
        raise Unavailable("process_unavailable") from None
    return sorted({anchor, *children})


def resolve_target(args):
    if args.session is not None:
        sid = sid_value(args.session)
        config = None
        if args.transcript is None:
            if args.root is None:
                raise Unavailable("explicit_source_required")
            config = str(config_root(os.environ, args.config_dir))
        return {"sid": sid, "pid": None, "proc_start": None,
                "root": str(args.root.resolve()) if args.root else None,
                "config": config, "identity_source": "explicit-session"}
    if args.pid is not None:
        return dict(bind_pid(args.pid, args), identity_source="official-pid")
    if args.tmux is not None:
        pane = pane_metadata(args)
        anchor = read_process(pane[1])
        if anchor["cwd"] != pane[2]:
            raise Unavailable("pane_process_mismatch")
        candidates = []
        for pid in descendant_pids(pane[1]):
            try:
                read_process(pid)
            except Unavailable as error:
                if error.reason == "process_not_live" and pid != pane[1]:
                    continue
                raise
            try:
                candidate = bind_pid(pid, args)
                if candidate["root"] != pane[2]:
                    raise Unavailable("pane_process_mismatch")
                candidates.append(candidate)
            except Unavailable as error:
                if error.reason not in ("official_record_missing", "official_not_interactive"):
                    raise
        if len(candidates) != 1:
            raise Unavailable("pane_target_not_unique")
        return dict(candidates[0], pane=pane, pane_process=anchor, identity_source="tmux-official")
    pid = os.getpid()
    visited = set()
    while pid > 0 and pid not in visited:
        visited.add(pid)
        process = read_process(pid)
        try:
            return dict(bind_pid(pid, args), identity_source="official-ancestor")
        except Unavailable as error:
            if error.reason not in ("official_record_missing", "official_not_interactive"):
                raise
        pid = process["parent"]
    raise Unavailable("interactive_ancestor_unavailable")


def recheck_target(target, args):
    if target["pid"] is None:
        return
    current = bind_pid(target["pid"], args)
    if any(current.get(key) != target.get(key) for key in current):
        raise Unavailable("target_identity_changed")
    if args.tmux is not None:
        again = resolve_target(args)
        if again != target:
            raise Unavailable("pane_identity_changed")


def transcript_path(target, args):
    if args.transcript is not None:
        return args.transcript.resolve()
    if not target.get("root") or not target.get("config"):
        raise Unavailable("explicit_source_required")
    mangled = re.sub(r"[^a-zA-Z0-9]", "-", target["root"])
    return Path(target["config"]) / "projects" / mangled / (target["sid"] + ".jsonl")


def stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def tail_bytes(stream, limit, size):
    start = max(0, size - limit)
    stream.seek(start)
    return stream.read(limit), start


def read_tail(path, limit):
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            data, start = tail_bytes(stream, limit, before.st_size)
            after = os.fstat(stream.fileno())
            if stamp(before) != stamp(after) or stamp(after) != stamp(path.stat()):
                raise Unavailable("transcript_changed", "pending")
    except OSError:
        raise Unavailable("transcript_unavailable") from None
    if data and not data.endswith(b"\n"):
        raise Unavailable("partial_tail", "pending")
    if start:
        cut = data.find(b"\n")
        if cut < 0 or cut == len(data) - 1:
            raise Unavailable("tail_limit_exceeded")
        data = data[cut + 1:]
    rows = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise Unavailable("invalid_jsonl") from None
        rows.append(row)
    return rows, before


def ignored(row):
    if any(row.get(key) for key in ("isSidechain", "isMeta", "isReplay", "isCompactSummary",
                                    "isSynthetic", "synthetic")):
        return True
    message = row.get("message")
    return isinstance(message, dict) and (bool(message.get("synthetic"))
            or message.get("model") in ("<synthetic>", "synthetic"))


def category(row):
    kind = row.get("type")
    if kind == "compact_boundary" or (kind == "system" and row.get("subtype") == "compact_boundary"):
        return "boundary"
    if kind in ("user", "tool"):
        return "input"
    if kind == "assistant":
        return "sample"
    if kind == "attachment" and isinstance(row.get("attachment"), dict):
        if row["attachment"].get("type") == "queued_command":
            return "queued"
    if kind == "queue-operation" and row.get("operation") == "enqueue":
        return "queue-event"
    return None


def input_counts(message):
    usage = message.get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                                          for key in INPUT_FIELDS):
        raise Unavailable("invalid_usage")
    return tuple(usage[key] for key in INPUT_FIELDS)


def semantic_time(value):
    try:
        if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError:
        raise Unavailable("usage_time_unavailable") from None
    return value


def native_groups(nodes, leaf):
    # Equivalence is local to a request's parent links, never global message.id.
    def request_id(row):
        message = row.get("message")
        value = message.get("id") if isinstance(message, dict) else None
        return value if isinstance(value, str) and value else None

    aliases, groups, origins = {}, {}, {}
    first_requests = {}
    referenced = {items[0][1].get("parentUuid") for items in nodes.values()}
    for key, items in nodes.items():
        index, row = items[0]
        parent = row.get("parentUuid")
        if parent in nodes and nodes[parent][0][0] >= index:
            raise Unavailable("chain_not_append_only")
        if category(row) == "queued" and key not in referenced and key != leaf:
            continue
        representative = key
        if category(row) == "sample":
            request = request_id(row)
            if isinstance(request, str) and request:
                first_requests.setdefault(request, index)
                if parent in aliases:
                    parent_row = groups[aliases[parent]][0][1]
                    if (category(parent_row) == "sample"
                            and request_id(parent_row) == request):
                        representative = aliases[parent]
                if representative == key:
                    representative = origins.setdefault((parent, request), key)
                if representative in groups:
                    counts = input_counts(groups[representative][0][1]["message"])
                    if any(input_counts(item[1]["message"]) != counts for item in items):
                        raise Unavailable("request_input_conflict")
        aliases[key] = representative
        groups.setdefault(representative, []).extend(items)

    def blocks(row):
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, list) else []

    declarations = {}
    for key, items in groups.items():
        if category(items[0][1]) != "sample":
            continue
        tools = {}
        for index, row in items:
            for block in blocks(row):
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and isinstance(block.get("id"), str) and block["id"]):
                    tools.setdefault(block["id"], index)
        declarations[key] = tools
    batches = {}
    for key, items in groups.items():
        index, row = items[0]
        parent = aliases.get(row.get("parentUuid"))
        content = blocks(row)
        if row.get("type") != "user" or not content or parent not in declarations:
            continue
        results = [block.get("tool_use_id") for block in content
                   if isinstance(block, dict) and block.get("type") == "tool_result"]
        tools = declarations[parent]
        if (len(results) == len(content) and all(isinstance(tool, str) and tool in tools
                                               and tools[tool] < index for tool in results)):
            batches.setdefault(parent, []).append((key, results))
    links = {key: aliases.get(items[0][1].get("parentUuid"), items[0][1].get("parentUuid"))
             for key, items in groups.items()}
    consumers, bridges = {}, {}
    for key, items in groups.items():
        index, row = items[0]
        parent = bridges.get(links[key], links[key])
        if category(row) == "queued":
            bridges[key] = parent
        elif category(row) == "sample" and first_requests.get(request_id(row)) == index:
            consumers.setdefault(parent, []).append(index)
    joined = {}
    for producer, batch in batches.items():
        result_ids = [tool for _, results in batch for tool in results]
        if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(declarations[producer]):
            continue
        last_result = max(index for key, _ in batch for index, _ in groups[key])
        if not any(index > last_result for key, _ in batch for index in consumers.get(key, [])):
            continue
        # Only a later, new request linked through this complete batch proves a join.
        representative = batch[0][0]
        for key, _ in batch[1:]:
            groups[representative].extend(groups.pop(key))
            joined[key] = representative
    aliases = {key: joined.get(value, value) for key, value in aliases.items()}
    links = {key: aliases.get(items[0][1].get("parentUuid"), items[0][1].get("parentUuid"))
             for key, items in groups.items()}
    return groups, links, aliases


def local_chain(rows, sid, leaf):
    nodes = {}
    events = []
    for index, row in enumerate(rows):
        if ignored(row):
            continue
        kind = category(row)
        if kind is None:
            continue
        if row.get("sessionId") != sid:
            raise Unavailable("transcript_session_mismatch")
        if kind in ("queue-event", "queued"):
            events.append(index)
            if kind == "queue-event":
                continue
        key = row.get("uuid")
        if not isinstance(key, str) or not key:
            raise Unavailable("unbound_compact_boundary" if kind == "boundary" else "chain_uuid_missing")
        parent = row.get("parentUuid")
        if parent is not None and (not isinstance(parent, str) or not parent):
            raise Unavailable("chain_parent_invalid")
        if key in nodes:
            old = nodes[key][0][1]
            if old.get("parentUuid") != parent or category(old) != kind:
                raise Unavailable("conflicting_uuid")
        nodes.setdefault(key, []).append((index, row))
    if not nodes:
        raise Unavailable("no_local_chain")
    nodes, links, aliases = native_groups(nodes, leaf)
    parents = set(links.values())
    heads = set(nodes) - parents
    automatic = leaf is None
    if not automatic:
        leaf = aliases.get(leaf, leaf)
    if automatic:
        if len(heads) != 1:
            raise Unavailable("chain_head_not_unique")
        leaf = next(iter(heads))
    if leaf not in nodes:
        raise Unavailable("leaf_unavailable")
    chain = []
    visited = set()
    key = leaf
    while key is not None and key in nodes:
        if key in visited:
            raise Unavailable("chain_cycle")
        visited.add(key)
        items = nodes[key]
        chain.extend(items)
        parent = links[key]
        if parent in nodes and nodes[parent][0][0] >= items[0][0]:
            raise Unavailable("chain_not_append_only")
        key = parent
    if automatic and visited != set(nodes):
        raise Unavailable("chain_disconnected")
    chain.sort(key=lambda item: item[0])
    return chain, events


def parse_snapshot(rows, sid, leaf=None):
    chain, queue_events = local_chain(rows, sid, leaf)
    generation = 0
    requests = {}
    last_input = None
    latest = None
    last_first = -1
    for index, row in chain:
        kind = category(row)
        if kind == "boundary":
            generation += 1
            latest = None
            last_input = None
            last_first = index
            continue
        if kind in ("input", "queued"):
            last_input = (index, kind)
            if latest is not None:
                latest["freshness"] = "lagging"
            continue
        message = row.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"]:
            raise Unavailable("request_id_missing")
        request = message["id"]
        counts = input_counts(message)
        if request in requests:
            previous = requests[request]
            if previous["counts"] != counts:
                raise Unavailable("request_input_conflict")
            if previous["generation"] != generation:
                continue
            continue
        if last_input is None and generation == 0 and not requests:
            requests[request] = {"counts": counts, "generation": generation}
            last_first = index
            continue
        if last_input is None or last_input[0] <= last_first:
            raise Unavailable("request_origin_unavailable")
        if last_input[1] == "queued":
            raise Unavailable("queued_request_origin_unsupported")
        if any(last_input[0] < event < index for event in queue_events):
            raise Unavailable("queued_request_origin_unsupported")
        latest = {"counts": counts, "generation": generation,
                  "used_tokens": sum(counts), "usage_at": semantic_time(row.get("timestamp")),
                  "freshness": "snapshot", "index": index}
        requests[request] = latest
        last_first = index
    if latest is None:
        raise Unavailable("compact_waiting_request" if generation else "usage_unavailable",
                          "pending" if generation else "unknown")
    if any(event > latest["index"] for event in queue_events):
        latest["freshness"] = "lagging"
    return {"used_tokens": latest["used_tokens"], "usage_at": latest["usage_at"],
            "freshness": latest["freshness"],
            "reason": "later_input_without_new_request" if latest["freshness"] == "lagging" else "api_input_snapshot"}


def parse_api_snapshot(rows, sid):
    # Reuse xcontext's latest input-only accounting, without claiming an active branch.
    requests = {}
    latest = None
    compacted = False
    has_input = False
    for row in rows:
        if ignored(row):
            continue
        kind = category(row)
        if kind is None:
            continue
        if row.get("sessionId") != sid:
            raise Unavailable("transcript_session_mismatch")
        if kind == "boundary":
            latest = None
            compacted = True
            has_input = False
            continue
        if kind in ("input", "queued", "queue-event"):
            has_input = True
            if latest is not None:
                latest["freshness"] = "lagging"
            continue
        message = row.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"]:
            raise Unavailable("request_id_missing")
        request = message["id"]
        counts = input_counts(message)
        if request in requests:
            if requests[request] != counts:
                raise Unavailable("request_input_conflict")
            continue
        requests[request] = counts
        if has_input:
            latest = {"used_tokens": sum(counts), "usage_at": semantic_time(row.get("timestamp")),
                      "freshness": "snapshot"}
    if latest is None:
        raise Unavailable("compact_waiting_request" if compacted else "usage_unavailable",
                          "pending" if compacted else "unknown")
    latest["reason"] = ("later_input_without_new_request_lineage_unverified"
                        if latest["freshness"] == "lagging"
                        else "xcontext_api_snapshot_lineage_unverified")
    return latest


def observe(args):
    result = {"label": args.label, "session_id": None, "source": None,
              "identity_source": None, "identity_verified": False, "pid": None,
              "proc_start": None, "usage_at": None,
              "observed_at": datetime.now(timezone.utc).isoformat(), "used_tokens": None,
              "window_tokens": args.window_tokens,
              "window_source": "argument" if args.window_tokens is not None else "unknown",
              "used_percentage": None, "freshness": "unknown", "reason": "source_unavailable",
              "file_size": None, "mtime_ns": None}
    target = None
    try:
        target = resolve_target(args)
        result.update(session_id=target["sid"], pid=target["pid"], proc_start=target["proc_start"],
                      identity_source=target["identity_source"])
        path = transcript_path(target, args)
        result["source"] = str(path)
        try:
            rows, metadata = read_tail(path, args.max_read_bytes)
            result.update(file_size=metadata.st_size, mtime_ns=metadata.st_mtime_ns)
            snapshot = (parse_snapshot(rows, target["sid"], args.leaf_uuid)
                        if args.mode == "strict" else parse_api_snapshot(rows, target["sid"]))
            if stamp(metadata) != stamp(path.stat()):
                raise Unavailable("transcript_changed", "pending")
        finally:
            recheck_target(target, args)
            result["identity_verified"] = target["pid"] is not None
        result.update(snapshot)
        if args.window_tokens is not None:
            result["used_percentage"] = round(100 * result["used_tokens"] / args.window_tokens, 2)
    except Unavailable as error:
        result.update(freshness=error.freshness, reason=error.reason,
                      used_tokens=None, used_percentage=None, usage_at=None)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        result.update(freshness="unknown", reason="source_unavailable",
                      used_tokens=None, used_percentage=None, usage_at=None)
    return result


def main(argv=None):
    print(json.dumps(observe(arguments(argv)), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
