#!/bin/bash
# session_driver.sh — shared session-driver detection + cc-connect helpers.
# version: 1.1.0 (2026-11-07) shadow-immune cc_session_key (3-level arbitration)
#          + self-context detect_driver (ancestor judge) + single-copy refactor.
#
# Self-contained: the gclear skill vendors this copy under lib/ and resolves it
# first; no external canonical location is required.
#
# Usage: source this file, then call detect_driver.
#   detect_driver [--root <project_root>]
#     -> echoes "cc-connect" or "tmux"; exits 1 (fail-closed) with a stderr
#        message when the mode cannot be proven.
#     --root scopes every probe to the given project root (cross-project
#        callers, e.g. mswitch): marker is read from <root>/.claude/settings.json,
#        cc probes run against <root>/.cc-connect/config.toml, tmux is probed
#        by session name. Without --root: self mode (cwd project + $TMUX_PANE).
#
# Detection is CROSS-VALIDATED (fail-closed, never guess). The settings.json
# env marker CC_SESSION_DRIVER is a diagnostic hint ONLY and never decides
# alone (2026-10-20 fix: a stale marker misrouted CLI-mode xclear to the
# cc-connect branch, which then died silently):
#   self mode (2026-11-07 Fix 0, caller-first, immune to CC_*/TMUX_* env
#   leakage in either direction — gclear never crosses modes, never
#   touches another caller's session):
#     1) process ancestry (<=10 hops) reaches THIS project's cc-connect daemon
#        -> cc-connect (the daemon spawned us; no stronger proof exists)
#     2) elif $TMUX_PANE + $TMUX AND that pane's process tree runs claude
#        -> tmux (pane-level precise, safe with multiple panes per project)
#     3) else fall through to the generic proofs below
#   --root mode keeps the original proof order:
#   "cc-connect" is proven only when ALL of:
#     a) a [[projects]] entry whose work_dir matches the scope root exists
#        in the scoped config.toml
#     b) the daemon is alive: management API reachable (TCP + Bearer token)
#     c) a session is online on the real telegram platform (sim/e2e residue
#        sessions never count)
#   "tmux" is proven only when ALL of:
#     self mode:   $TMUX_PANE + $TMUX set AND that pane's process tree runs claude
#     --root mode: a tmux session named <project> (or <project>-cli, the
#        collision-avoidance suffix used by cc_connect_cli_switch) exists
#        AND one of its panes runs claude
#   otherwise: refuse to act (exit 1).
#
# cc-connect helpers (only meaningful in cc-connect mode):
#   cc_mgmt_base                     -> http://127.0.0.1:<port>
#   cc_mgmt_token                    -> bearer token from scoped config.toml
#   cc_api <METHOD> <path> [json]    -> response body on stdout, HTTP code on stderr
#   cc_project_for_dir <dir>         -> project name or "" (matches config work_dir)
#   cc_session_key <project> [chat_id] -> the real telegram session_key;
#                                       exit 2 = none, exit 3 = ambiguous
#                                       (candidates on stderr; chat_id disambiguates)
#   cc_agent_sid <project> [skey]    -> agent_session_id from session detail
#   cc_active_session_id <project> [skey] -> cc-connect session id (e.g. s1)
#   cc_inject <project> <skey> <msg> -> 0 delivered | 1 transport/API failure
#                                       | 2 busy/transient (retry makes sense)
#                                       | 3 addressing failure (fatal, never retry)

# Resolve the directory that identifies "the project" for detection.
__sd_work_dir() {
    if [ -n "${CC_WORK_DIR:-}" ]; then echo "$CC_WORK_DIR"; return; fi
    local top
    top=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$top" ] && { echo "$top"; return; }
    pwd -P
}

__sd_python() {
    # Needs tomllib (py3.11+). Probe candidates once, cache in env.
    # Never trust an inherited/cache name without re-testing the capability;
    # .env/.venv are discovered from the root derived upward from THIS file's
    # location (vendored-copy safe) in addition to CC_WORK_DIR/$PWD; no blind
    # python3 fallback — finding nothing compatible is a hard error.
    local cand envp="" root src_root _r
    root="${CC_WORK_DIR:-$PWD}"
    src_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || src_root=""
    while [ -n "$src_root" ] && [ "$src_root" != / ] && [ ! -d "$src_root/.git" ] && [ ! -f "$src_root/CLAUDE.md" ]; do
        src_root="${src_root%/*}"; [ -n "$src_root" ] || src_root=/
    done
    [ "$src_root" = / ] && src_root=""
    for _r in "$root" "$src_root"; do
        [ -n "$_r" ] && [ -r "$_r/.env" ] || continue
        envp=$(sed -nE 's/^[A-Za-z0-9_]+_PYTHON=["]?([^"]*)["]?[[:space:]]*$/\1/p' "$_r/.env" 2>/dev/null | head -1)
        [ -n "$envp" ] && break
    done
    for cand in "${CC_PYTHON:-}" "${__SD_PYTHON_RESOLVED:-}" "$envp" \
                "$src_root/.venv/bin/python" "$(command -v python3 2>/dev/null)" python3; do
        [ -n "$cand" ] || continue
        command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ] || continue
        if "$cand" -c 'import sys, tomllib; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
            __SD_PYTHON_RESOLVED="$cand"
            printf '%s\n' "$cand"
            return 0
        fi
    done
    echo 'ERROR: no compatible Python interpreter (requires Python 3.11+ with tomllib); set CC_PYTHON or <NAME>_PYTHON in the project .env.' >&2
    return 1
}

__sd_config_path() {
    # Resolution order: env override → project instance config (search upward from $PWD for
    # .cc-connect/config.toml, covering subdirectory invocations; per-project instance split
    # since 2026-10-20) → shared path (rollback compatibility).
    # 2026-10-21 environment pollution protection: with two instances deployed (one daemon +
    # config per project), the session env's CC_CONNECT_CONFIG may be left over from **another
    # project's** config (measured: session A's CC_CONNECT_CONFIG pointed at B's config while
    # A's daemon actually ran A's instance config) → if the env-referenced config does not
    # contain this project's work_dir (per $dir), treat it as pollution and fall back to
    # instance probing. Pass the dir argument to validate.
    local dir="${1:-}"
    if [ -n "${CC_CONNECT_CONFIG:-}" ]; then
        if [ -z "$dir" ] || __sd_config_has_workdir "$CC_CONNECT_CONFIG" "$dir"; then
            echo "$CC_CONNECT_CONFIG"; return
        fi
    fi
    local d
    d="${dir:-$(pwd)}"   # cross-directory lookup must search upward from dir, not the caller's cwd
    for _ in 1 2 3 4; do
        [ -r "$d/.cc-connect/config.toml" ] && { echo "$d/.cc-connect/config.toml"; return; }
        [ "$d" = "/" ] && break
        d=$(dirname "$d")
    done
    echo "$HOME/.cc-connect/config.toml"
}

# __sd_config_has_workdir <config> <dir> -> 0 if some project's agent.options.work_dir == dir
__sd_config_has_workdir() {
    [ -r "$1" ] || return 1
    "$(__sd_python)" - "$1" "$2" <<'PYEOF' 2>/dev/null
import sys
try:
    import tomllib
except ImportError:
    sys.exit(1)
cfg_path, want = sys.argv[1], sys.argv[2].rstrip('/')
with open(cfg_path, 'rb') as f:
    data = tomllib.load(f)
for p in data.get('projects', []):
    wd = str(p.get('agent', {}).get('options', {}).get('work_dir', '')).rstrip('/')
    if wd and wd == want:
        sys.exit(0)
sys.exit(1)
PYEOF
}

# Settings.json env marker of a project root, read from the FILE (never the
# caller's process env — ambient env cannot describe a target project).
# Prints the marker value; empty when missing/unreadable.
__sd_marker_for_root() {
    local f="$1/.claude/settings.json"
    [ -r "$f" ] || return 0
    "$(__sd_python)" - "$f" <<'PYEOF' 2>/dev/null
import json, sys
try:
    env = json.load(open(sys.argv[1])).get("env") or {}
    v = env.get("CC_SESSION_DRIVER")
    if isinstance(v, str):
        print(v)
except Exception:
    pass
PYEOF
}

__sd_mgmt_from_config() {
    # prints "<port>\n<token>" ; empty output when management API not configured
    local cfg
    cfg=$(__sd_config_path)
    [ -r "$cfg" ] || return 1
    local port token
    port=$(sed -n '/^\[management\]/,/^\[/{s/^port *= *"\?\([0-9]*\)"\?/\1/p;}' "$cfg" | head -1)
    token=$(sed -n '/^\[management\]/,/^\[/{s/^token *= *"\([^"]*\)".*/\1/p;}' "$cfg" | head -1)
    [ -n "$port" ] && [ -n "$token" ] || return 1
    printf '%s\n%s\n' "$port" "$token"
}

cc_mgmt_base() {
    local out port
    out=$(__sd_mgmt_from_config) || return 1
    port=$(printf '%s' "$out" | head -1)
    echo "http://127.0.0.1:${port}"
}

cc_mgmt_token() {
    local out
    out=$(__sd_mgmt_from_config) || return 1
    printf '%s' "$out" | sed -n '2p'
}

# cc_api METHOD path [json-body]
#   stdout: response body
#   stderr: "HTTP:<code>" (or "CURL_FAIL" on transport error)
cc_api() {
    local method="$1" path="$2" body="${3:-}"
    local base token curl_out http_code
    base=$(cc_mgmt_base) || { echo "CURL_FAIL" >&2; return 1; }
    token=$(cc_mgmt_token) || { echo "CURL_FAIL" >&2; return 1; }
    if [ -n "$body" ]; then
        curl_out=$(curl -sS -m 15 -X "$method" -H "Authorization: Bearer $token" \
            -H "Content-Type: application/json" -d "$body" "$base$path" 2>&1)
    else
        curl_out=$(curl -sS -m 15 -X "$method" -H "Authorization: Bearer $token" "$base$path" 2>&1)
    fi
    http_code=$?
    if [ "$http_code" -ne 0 ]; then
        echo "CURL_FAIL" >&2
        return 1
    fi
    printf '%s' "$curl_out"
    return 0
}

# Exact work_dir match against [[projects]] blocks in config.toml.
cc_project_for_dir() {
    local dir="${1:-$(__sd_work_dir)}"
    local cfg
    cfg=$(__sd_config_path "$dir")
    [ -r "$cfg" ] || return 1
    "$(__sd_python)" - "$cfg" "$dir" <<'PYEOF' 2>/dev/null
import sys
try:
    import tomllib
except ImportError:
    sys.exit(1)
cfg_path, want = sys.argv[1], sys.argv[2].rstrip('/')
with open(cfg_path, 'rb') as f:
    data = tomllib.load(f)
for p in data.get('projects', []):
    opts = p.get('agent', {}).get('options', {})
    wd = str(opts.get('work_dir', '')).rstrip('/')
    if wd and wd == want:
        print(p.get('name', ''))
        sys.exit(0)
sys.exit(1)
PYEOF
}

# cc_session_key <project> [chat_id]
#   Print the session_key of the project's REAL telegram session.
#   2026-10-20 fix: the old "first active session" pick was poisoned by sim/e2e
#   residue (active=true too), producing injection failures like
#   `platform "sim" not found`. Now: telegram: prefix is mandatory; an
#   optional chat_id (from the triggering message envelope) narrows further.
#   2026-10-22 fix: dead duplicate ROWS under the same session_key are normal
#   daemon residue (e.g. a cron/injection turn leaves an inactive entry) and
#   must never break address resolution. The job here is resolving an ADDRESS,
#   so: prefer keys that have an active row, collapse same-key duplicates, and
#   only report ambiguity when truly DISTINCT keys compete. Missing "active"
#   fields (schema drift) degrade gracefully to distinct-key collapse.
cc_session_key() {
    local proj="$1" chat_id="${2:-}" resp
    resp=$(cc_api GET "/api/v1/projects/$proj/sessions") || return 1
    printf '%s' "$resp" | CC_SESSION_CHAT_ID="$chat_id" "$(__sd_python)" -c '
import json, os, sys
try:
    data = json.load(sys.stdin)["data"]
    d = data["sessions"]
except Exception:
    sys.exit(1)
# 2026-11-07 shadow immunity: daemon-authoritative active_keys (L2), optional
# (schema drift on older daemons degrades to empty).
ak = [k for k in (data.get("active_keys") or {}) if str(k).startswith("telegram:")]
tg = [s for s in d if str(s.get("session_key", "")).startswith("telegram:")]
if not tg:
    sys.stderr.write("cc_session_key: no telegram session (all %d are sim/other)\n" % len(d))
    sys.exit(2)
cid = os.environ.get("CC_SESSION_CHAT_ID", "")
if cid:
    exact = [s for s in tg if cid in str(s.get("session_key", "")).split(":")]
    if exact:
        tg = exact
all_keys, act_keys = [], []
for s in tg:
    k = str(s.get("session_key"))
    if k not in all_keys:
        all_keys.append(k)
    if s.get("active") and k not in act_keys:
        act_keys.append(k)
# Key structure is stateless truth (2026-11-07): real user sessions always
# carry a :user suffix; cron/injection traffic creates BARE keys
# (telegram:<chat>) whose row-level `active` flag is never cleared, so the
# shadow can sit in act_keys next to the real key (or alone after a restart).
suffixed = [k for k in all_keys if k.count(":") >= 2]
bare = [k for k in all_keys if k not in suffixed]
if len(act_keys) == 1:
    k = act_keys[0]
    if k in bare and len(suffixed) == 1:
        print(suffixed[0])   # W2 guard: never silently target the bare key
                             # when the unique real key is identifiable.
    else:
        print(k)
elif len(act_keys) > 1 or (len(act_keys) == 0 and len(all_keys) > 1):
    # 3-level arbitration (this branch was unconditional fatal before 1.1.0):
    if len(suffixed) == 1:
        print(suffixed[0])            # L1 stateless structure — daemon-version agnostic
    elif len(ak) == 1:
        print(ak[0])                  # L2 daemon-authoritative active_keys
    else:
        live = sorted({str(s.get("session_key")) for s in tg if s.get("live")})
        if len(live) == 1:
            print(live[0])            # L3 single live row
        else:
            sys.stderr.write("cc_session_key: %d telegram keys unresolved (suffixed=%d active_keys=%d live=%d): %s\n"
                             % (len(all_keys), len(suffixed), len(ak), len(live), ", ".join(sorted(all_keys))))
            sys.exit(3)
elif len(all_keys) == 1:
    print(all_keys[0])
else:
    sys.stderr.write("cc_session_key: no active telegram session; %d distinct keys ambiguous: %s\n"
                     % (len(all_keys), ", ".join(sorted(all_keys))))
    sys.exit(3)
' 2>/dev/null
}

cc_active_session_id() {
    local proj="$1" skey="${2:-}" resp
    resp=$(cc_api GET "/api/v1/projects/$proj/sessions") || return 1
    printf '%s' "$resp" | "$(__sd_python)" -c '
import json, sys
skey = sys.argv[1]
try:
    d = json.load(sys.stdin)["data"]["sessions"]
except Exception:
    sys.exit(1)
pool = [s for s in d if str(s.get("session_key", "")).startswith("telegram:")]
if skey:
    pool = [s for s in pool if s.get("session_key") == skey]
if not pool:
    sys.exit(1)
# 2026-10-22: same-key residue rows are normal; pick deterministically —
# active rows first, then most recently updated, never registry order luck.
recency = sorted(pool, key=lambda s: str(s.get("updated_at", "")), reverse=True)
act = [s for s in recency if s.get("active")]
print(act[0]["id"] if act else recency[0]["id"])
' 2>/dev/null "$skey"
}

# cc_agent_sid <project> [session_key]
cc_agent_sid() {
    local proj="$1" skey="${2:-}" sid resp
    sid=$(cc_active_session_id "$proj" "$skey") || return 1
    [ -n "$sid" ] || return 1
    resp=$(cc_api GET "/api/v1/projects/$proj/sessions/$sid") || return 1
    printf '%s' "$resp" | "$(__sd_python)" -c "
import json,sys
try:
    print(json.load(sys.stdin)['data'].get('agent_session_id') or '')
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# cc_inject <project> <session_key> <message>
#   Deliver a prompt INTO a session (agent receives it as a user turn).
#   v1.5.0 reality (Phase 0 / pilot verified): POST /projects/{p}/send and the
#   `cc-connect send` CLI are OUTBOUND primitives (AI -> IM) and silently
#   no-op for inbound delivery. The only working remote prompt-injection
#   channel is a one-shot cron job: add (disabled) -> exec -> delete.
#   Exit codes (2026-10-20 fix — callers must not retry blindly):
#     0  delivered
#     1  transport/API failure (curl, no cron job created, exec not accepted)
#     2  busy / transient async failure — retry makes sense
#     3  addressing failure (platform/session not found) — retry NEVER helps
#   The concrete server reason is always echoed to stderr.
# A high-resolution unique stamp for cron descriptions. `date +%s%N` is a GNU
# extension (macOS prints a literal "N"), so prefer Python's nanosecond clock and
# fall back to epoch seconds + $RANDOM.
_sd_unique_stamp() {
    "$(__sd_python)" -c 'import time; print(time.time_ns())' 2>/dev/null \
        || printf '%s%s\n' "$(date +%s)" "$RANDOM"
}

cc_inject() {
    local proj="$1" skey="$2" msg="$3"
    local body cid resp stamp
    stamp=$(_sd_unique_stamp)
    body=$("$(__sd_python)" -c 'import json,sys; print(json.dumps({
        "project": sys.argv[1], "session_key": sys.argv[2], "prompt": sys.argv[3],
        "cron_expr": "0 5 1 1 *", "description": "session-driver inject " + sys.argv[4],
        "enabled": False, "silent": True}))' "$proj" "$skey" "$msg" "$stamp" 2>/dev/null) || { echo "cc_inject: payload build failed" >&2; return 1; }
    resp=$(cc_api POST /api/v1/cron "$body") || { echo "cc_inject: cron create transport failure" >&2; return 1; }
    cid=$(printf '%s' "$resp" | "$(__sd_python)" -c "
import json,sys
try:
    print(json.load(sys.stdin)['data']['id'])
except Exception:
    sys.exit(1)
" 2>/dev/null) || { echo "cc_inject: cron create rejected: $(printf '%s' "$resp" | head -c 200)" >&2; return 1; }
    [ -n "$cid" ] || return 1
    # Creation ignores enabled=false in v1.5.0; confirm an explicit disable.
    if ! resp=$(cc_api PATCH "/api/v1/cron/$cid" '{"enabled":false}') || ! printf '%s' "$resp" | "$(__sd_python)" -c 'import json,sys; sys.exit(json.load(sys.stdin).get("data", {}).get("enabled") is not False)' 2>/dev/null; then
        cc_api DELETE "/api/v1/cron/$cid" >/dev/null 2>&1
        echo "cc_inject: disable not confirmed; exec not attempted" >&2
        return 1
    fi
    if ! resp=$(cc_api POST "/api/v1/cron/$cid/exec"); then
        cc_api DELETE "/api/v1/cron/$cid" >/dev/null 2>&1
        echo "cc_inject: exec transport failure" >&2
        return 1
    fi
    if [ -z "$(printf '%s' "$resp" | grep '"triggered"')" ]; then
        cc_api DELETE "/api/v1/cron/$cid" >/dev/null 2>&1
        echo "cc_inject: exec not accepted: $(printf '%s' "$resp" | head -c 200)" >&2
        return 1
    fi
    # exec acks "triggered" synchronously but the run itself can fail async
    # (e.g. "session is busy"). Probe the job's last_error briefly so callers
    # can classify: busy (2, retry) vs addressing (3, fatal).
    local _i _err
    for _i in 1 2 3; do
        sleep 2
        resp=$(cc_api GET /api/v1/cron) || break
        _err=$(printf '%s' "$resp" | "$(__sd_python)" -c "
import json,sys
try:
    for j in json.load(sys.stdin)['data']['jobs']:
        if j.get('id') == sys.argv[1]:
            print(j.get('last_error') or '')
            break
except Exception:
    pass
" 2>/dev/null "$cid")
        if [ -n "$_err" ]; then
            cc_api DELETE "/api/v1/cron/$cid" >/dev/null 2>&1
            case "$_err" in
                *busy*)
                    echo "cc_inject: session busy: $_err" >&2
                    return 2 ;;
                *"not found"*|*"no session"*|*"unknown platform"*|*"no active"*)
                    echo "cc_inject: addressing failure (retry useless): $_err" >&2
                    return 3 ;;
                *)
                    echo "cc_inject: async run failed: $_err" >&2
                    return 2 ;;
            esac
        fi
    done
    cc_api DELETE "/api/v1/cron/$cid" >/dev/null 2>&1
    return 0
}

# ── tmux evidence helpers ────────────────────────────────────────────────
__sd_tmux_pane_has_claude() { # $1=pane_pid — recursive child walk
    local pid="$1" cur child
    cur=$(ps -o cmd= -p "$pid" 2>/dev/null)
    case "$cur" in *claude*) return 0 ;; esac
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        __sd_tmux_pane_has_claude "$child" && return 0
    done
    return 1
}

__sd_tmux_session_has_claude() { # $1=session name — any pane runs claude
    local p
    command -v tmux >/dev/null 2>&1 || return 1
    for p in $(tmux list-panes -t "$1" -F '#{pane_pid}' 2>/dev/null); do
        __sd_tmux_pane_has_claude "$p" && return 0
    done
    return 1
}

# All three cc-mode proofs for the currently-scoped project (env-scoped by
# CC_CONNECT_CONFIG / CC_WORK_DIR). Silent; rc=0 iff proven.
__sd_cc_proven() {
    local proj
    proj=$(cc_project_for_dir "$(__sd_work_dir)") || return 1
    [ -n "$proj" ] || return 1
    cc_api GET /api/v1/status >/dev/null 2>&1 || return 1
    cc_session_key "$proj" >/dev/null 2>&1 || return 1
    return 0
}

# True when $2 is a session_key present in project $1's daemon session table
# (2026-11-07 Fix 0 key-level self-reference support for gclear).
__sd_key_in_project_sessions() {
    local proj="$1" want="$2" resp
    [ -n "$want" ] || return 1
    resp=$(cc_api GET "/api/v1/projects/$proj/sessions") || return 1
    printf '%s' "$resp" | "$(__sd_python)" -c '
import json, sys
want = sys.argv[1]
try:
    d = json.load(sys.stdin)["data"]["sessions"]
except Exception:
    sys.exit(1)
sys.exit(0 if any(str(s.get("session_key")) == want for s in d) else 1)
' 2>/dev/null "$want"
}

# basename of a process's comm: macOS `ps` returns a full path, Linux a short
# name; normalising here keeps tmux / cc-connect matching identical everywhere.
__sd_comm_name() {   # <pid>
    local comm
    comm=$(ps -ww -o comm= -p "$1" 2>/dev/null) || return 1
    [ -n "$comm" ] || return 1
    printf '%s\n' "${comm##*/}"
}

# True when the calling process's ancestry (<=10 hops) includes a cc-connect
# daemon whose --config points at the currently-scoped project config.
# Deterministic self-context proof (2026-11-07 Fix 0): immune to CC_*/TMUX_*
# env leakage in BOTH directions, because the daemon ancestor relationship
# cannot be faked by inherited environment variables.
# Portable: `ps` replaces /proc reads (macOS has no /proc). args are matched as
# space-delimited tokens (a config path containing spaces conservatively fails
# to match, which never mis-routes).
__sd_ancestor_is_cc_daemon() {
    local p="${BASHPID:-$$}" q i cfg comm args
    cfg=$(__sd_config_path) || return 1
    [ -n "$cfg" ] || return 1
    for i in 1 2 3 4 5 6 7 8 9 10; do
        q=$(ps -ww -o ppid= -p "$p" 2>/dev/null | tr -d '[:space:]')
        case "$q" in ''|*[!0-9]*) return 1 ;; esac
        [ "$q" -gt 1 ] || return 1
        comm=$(__sd_comm_name "$q") || return 1
        case "$comm" in
            tmux|tmux:*)
                # tmux server boundary: panes are server-managed; the server's
                # own ancestry proves nothing about daemon spawning (a server
                # first started from a daemon-side shell would otherwise
                # poison every pane's chain). Stop walking.
                return 1 ;;
            cc-connect*)
                args=$(ps -ww -o args= -p "$q" 2>/dev/null)
                case "$args " in
                    *" $cfg "*) return 0 ;;
                    *" --config=$cfg "*) return 0 ;;
                esac ;;
        esac
        p=$q
    done
    return 1
}

__sd_detect_scoped() { # $1=root ("" = self mode)
    local root="$1" marker tmux_ok=1
    marker=$(__sd_marker_for_root "${root:-$(__sd_work_dir)}")
    # a0) self-context priority (2026-11-07 Fix 0; self mode ONLY — cross-project
    #     --root mode keeps the original proof order below). The caller's own
    #     runtime context decides, immune to env leakage in either direction:
    if [ -z "$root" ]; then
        # 1) ancestry reaches this project's cc-connect daemon -> cc self
        if __sd_ancestor_is_cc_daemon; then
            echo "cc-connect"
            return 0
        fi
        # 2) elif this tmux pane runs claude -> tmux self (pane-level precise)
        if [ -n "${TMUX_PANE:-}" ] && [ -n "${TMUX:-}" ]; then
            local pane_pid
            pane_pid=$(tmux display-message -p -t "$TMUX_PANE" '#{pane_pid}' 2>/dev/null)
            if [ -n "$pane_pid" ] && __sd_tmux_pane_has_claude "$pane_pid"; then
                echo "tmux"
                return 0
            fi
        fi
    fi
    # 3) generic proofs (unchanged): cc first, then tmux evidence, fail-closed
    # a) cc-connect: daemon alive + this root registered + telegram session online
    if __sd_cc_proven; then
        echo "cc-connect"
        return 0
    fi
    # b) tmux: runtime evidence only; ambient $TMUX_PANE alone never decides
    if [ -n "$root" ]; then
        # cross-project: probe by session name (+ -cli collision-avoidance suffix)
        local pname base
        pname=$(basename "$root" | tr -d '_' | tr 'A-Z' 'a-z')
        local conf_name
        conf_name=$(cc_project_for_dir "$root" 2>/dev/null)
        [ -n "$conf_name" ] && pname="$conf_name"
        for base in "$pname" "${pname}-cli"; do
            if tmux has-session -t "$base" 2>/dev/null && __sd_tmux_session_has_claude "$base"; then
                echo "tmux"
                return 0
            fi
        done
    elif [ -n "${TMUX_PANE:-}" ] && [ -n "${TMUX:-}" ]; then
        local pane_pid
        pane_pid=$(tmux display-message -p -t "$TMUX_PANE" '#{pane_pid}' 2>/dev/null)
        if [ -n "$pane_pid" ] && __sd_tmux_pane_has_claude "$pane_pid"; then
            echo "tmux"
            return 0
        fi
    fi
    # fail-closed — marker is reported for diagnosis, never trusted
    echo "ERROR: cannot determine session driver (marker='${marker:-none}' unbacked: no cc-connect daemon with online session, no tmux claude). Refusing to act." >&2
    return 1
}

detect_driver() {
    local root=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --root) root="${2:-}"; shift 2 ;;
            *) echo "detect_driver: unknown arg: $1" >&2; return 1 ;;
        esac
    done
    if [ -n "$root" ]; then
        [ -d "$root" ] || { echo "detect_driver: --root is not a directory: $root" >&2; return 1; }
        local cfg="$root/.cc-connect/config.toml"
        [ -r "$cfg" ] || { echo "detect_driver: no .cc-connect/config.toml under $root" >&2; return 1; }
        CC_CONNECT_CONFIG="$cfg" CC_WORK_DIR="$root" __sd_detect_scoped "$root"
    else
        __sd_detect_scoped ""
    fi
}
