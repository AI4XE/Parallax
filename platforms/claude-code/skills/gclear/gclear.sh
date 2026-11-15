#!/bin/bash
# gclear.sh — cross-session context handoff (old session -> /clear -> clean session)
#
# gclear 1.2.4: shared kernel + CC/CLI adapters. Correctness comes from
# post-delivery verification and staged, idempotent retries, never from the
# driver's "accepted". Copy this whole skill directory into any Claude Code
# project and it works; paths and credentials are derived at runtime.
#
# Must be launched detached (setsid) so /clear cannot tree-kill it.
#
# Interface:
#   gclear.sh "<prompt>" [--context <path>] [--no_file] [--model <name>]
#             [--resume-inject]

set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
ROOT="$(cd "$SELF_DIR/../../.." && pwd -P)"
export CC_WORK_DIR="$ROOT"

# ── entry capture BEFORE any defaults are applied ───────────────────────
GCLEAR_EXTERNAL_DEADLINE="${GCLEAR_DEADLINE:-}"
GCLEAR_EXTERNAL_OPERATION="${GCLEAR_OPERATION:-}"
export GCLEAR_EXTERNAL_DEADLINE GCLEAR_EXTERNAL_OPERATION

DEFAULT_CONTEXT="$SELF_DIR/gclear_context.md"
LEDGER="$SELF_DIR/gclear.log"
MARK_TOKEN=""
STATUS="unknown"
VERIFIED_BY="-"
SUBMIT_RESULT="-"
MODEL_RESULT="-"
CLEAR_RESULT="not-attempted"
INJECT_RESULT="not-attempted"
NOTIFY_SENT="no"
SUBMIT_TS=""
INJECT=""
OLD_SID=""
CC_NEW_SID=""
new_sid=""
RESUME_INJECT=0
CLEAR_ONLY=0
DRIVER=""

ts() { date '+%H:%M:%S'; }
log() { echo "[gclear $(ts)] $*"; }

# ── configuration (single shared gclear.conf; env wins) ───────────────────
# shellcheck source=gclear_config.sh
. "$SELF_DIR/gclear_config.sh"
gclear_config_load
gclear_credentials
WSTMP="${WSTMP:-$SELF_DIR/.tmp}"
mkdir -p "$WSTMP" 2>/dev/null

# ── notification (never blocks) ───────────────────────────────────────────
NOTIFY_TOKEN="$BOT_TOKEN"
NOTIFY_CHAT="$CHAT_ID"
notify() {
    if [ "${GCLEAR_MINIMAL_NOTIFY:-0}" = 1 ]; then
        if [ "${STATUS:-unknown}" = success ]; then
            set -- "${GCLEAR_CLEAR_MESSAGE:-Context cleared}"
        else
            set -- "${GCLEAR_CLEAR_FAILURE:-Clear not confirmed}"
        fi
    fi
    log "notification content: $1"
    if [ "${GCLEAR_NOTIFY_DRYRUN:-0}" = 1 ]; then log "DRYRUN notify (not sent)"; NOTIFY_SENT="dryrun"; return; fi
    if [ -z "$NOTIFY_TOKEN" ] || [ -z "$NOTIFY_CHAT" ]; then
        log "WARN: no token/chat_id, skipping notification (non-blocking)"; NOTIFY_SENT="skip(no-cred)"; return
    fi
    if curl -s --max-time 10 "https://api.telegram.org/bot${NOTIFY_TOKEN}/sendMessage" \
        -d "chat_id=${NOTIFY_CHAT}" --data-urlencode "text=$1" 2>/dev/null |
        "$CC_PYTHON" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") is True else 1)' 2>/dev/null; then
        NOTIFY_SENT="yes"
    else
        log "WARN: notification send failed (non-blocking)"; NOTIFY_SENT="fail"
    fi
}

# ── interpreter ───────────────────────────────────────────────────────────
# shellcheck source=handoff.sh
. "$SELF_DIR/handoff.sh"
# shellcheck source=gclear_state.sh
. "$SELF_DIR/gclear_state.sh"
if ! CC_PYTHON="$(gclear_python)"; then
    STATUS="fail(interpreter)"; echo "ERROR: no compatible Python (3.11+/tomllib)" >&2; exit 2
fi
export CC_PYTHON

# ── vendored session driver first, shared fallback (never modified) ──────
if [ -z "${SD_PATH:-}" ]; then
    for _sd in "$SELF_DIR/lib/session_driver.sh" "$ROOT/scripts/lib/session_driver.sh"; do
        [ -f "$_sd" ] && { SD_PATH="$_sd"; break; }
    done
fi
SD_PATH="${SD_PATH:-}"
if [ -z "$SD_PATH" ]; then
    log "ERROR: session_driver.sh not found (vendored copy missing)"
    echo "ERROR: session_driver.sh not found; refusing to act" >&2
    exit 2
fi
# shellcheck disable=SC1090
. "$SD_PATH"
export CC_CONNECT_CONFIG="$(__sd_config_path "$ROOT" 2>/dev/null || true)"

# ── model names (generic validation + optional conf alias mapping) ────────
# Validation accepts ANY non-empty, whitespace-free string (only relaxed, never
# tightened). Resolution: MODEL_ALIASES in gclear.conf maps short aliases to
# full IDs at runtime (space-separated k=v pairs); full IDs starting with
# "claude-" pass through unchanged (pattern rule lives in code, not in conf);
# anything unmapped resolves to empty -> the caller's dynamic path handles it
# (no hard failure, no mismatch warning).
model_is_valid() {
    case "$1" in
        ""|*[[:space:]]*) return 1 ;;
        *) return 0 ;;
    esac
}
model_resolve() {
    local pair k v
    for pair in ${MODEL_ALIASES:-}; do
        k="${pair%%=*}"; v="${pair#*=}"
        [ -n "$k" ] && [ "$v" != "$pair" ] || continue
        [ "$k" = "$1" ] && { printf '%s\n' "$v"; return 0; }
    done
    case "$1" in
        claude-*) printf '%s\n' "$1" ;;
        *)        printf '' ;;
    esac
}

usage() {
    cat <<EOF
gclear.sh — cross-session context handoff (old session -> /clear -> clean session)

Usage:
  gclear.sh "<injection prompt>" [--context <path>] [--no_file] [--model <name>] [--resume-inject]

Arguments:
  <prompt>              the sentence given to the new session; omitted → built-in default (includes the context file reference)
  --context <path>      context file path; default <skill-dir>/gclear_context.md
  --no_file             clear + inject only, without attaching a context file
  --model <name>        switch to this model after /clear succeeds, then inject; omitted → no model switch
  --resume-inject       inject the prompt only, no clear (for when clear took effect but injection was not confirmed)
  -h, --help            print this help

--model accepted values:
  Any non-empty string without whitespace. Alias mappings are configured in gclear.conf's
  MODEL_ALIASES (space-separated k=v pairs, empty by default); a full model ID starting with
  claude-* is passed through as-is; unmapped names switch as a dynamic target (no allowlist
  check; the receipt is authoritative for the result).
EOF
}

# ── args ──────────────────────────────────────────────────────────────────
INJECT_RAW=""; CONTEXT_FILE=""; NO_FILE=0; MODEL_ARG=""; _got_positional=0
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --context) shift; [ $# -gt 0 ] || { log "ERROR: --context requires a path argument"; exit 2; }; CONTEXT_FILE="$1" ;;
        --context=*) CONTEXT_FILE="${1#--context=}" ;;
        --model) shift; [ $# -gt 0 ] || { log "ERROR: --model requires an argument"; exit 2; }; MODEL_ARG="$1" ;;
        --model=*) MODEL_ARG="${1#--model=}" ;;
        --no_file) NO_FILE=1 ;;
        --clear-only) CLEAR_ONLY=1; NO_FILE=1 ;;
        --resume-inject) RESUME_INJECT=1 ;;
        --) shift; while [ $# -gt 0 ]; do
                [ "$_got_positional" -eq 0 ] && { INJECT_RAW="$1"; _got_positional=1; } || { log "ERROR: unexpected extra positional argument: $1"; exit 2; }
                shift; done; break ;;
        -*) log "ERROR: unknown option: $1"; exit 2 ;;
        *) if [ "$_got_positional" -eq 0 ]; then INJECT_RAW="$1"; _got_positional=1; else log "ERROR: unexpected extra positional argument: $1"; exit 2; fi ;;
    esac
    shift
done
[ "$NO_FILE" -eq 0 ] && [ -z "$CONTEXT_FILE" ] && CONTEXT_FILE="$DEFAULT_CONTEXT"

if [ -n "$MODEL_ARG" ] && ! model_is_valid "$MODEL_ARG"; then
    log "ERROR: invalid --model value: '$MODEL_ARG' (must be non-empty and whitespace-free)"
    echo "ERROR: invalid --model value: '$MODEL_ARG' (must be non-empty and whitespace-free)" >&2; usage; exit 2
fi
# --clear-only is an explicit narrow mode (clear, never inject). It can never be
# triggered by the normal handoff path, and rejects a prompt/--model combination.
if [ "$CLEAR_ONLY" -eq 1 ] && [ "$RESUME_INJECT" -eq 1 ]; then
    log "ERROR: --clear-only and --resume-inject are mutually exclusive"; exit 2
fi
if [ "$CLEAR_ONLY" -eq 1 ] && { [ -n "$MODEL_ARG" ] || [ "$_got_positional" -eq 1 ]; }; then
    log "ERROR: --clear-only does not accept a prompt or --model"; exit 2
fi

# ── prompt layering + single line + marker ────────────────────────────────
NOTICE=""; CTX_REF=""; CONTEXT_BASENAME=""
if [ "$NO_FILE" -eq 0 ]; then
    CONTEXT_BASENAME="$(basename "$CONTEXT_FILE")"
    # Portable realpath: --relative-to only for the first form; the second form
    # is canonicalisation WITHOUT relativisation (different semantics).
    CTX_REF=$("$CC_PYTHON" "$SELF_DIR/handoff.py" path --path "$CONTEXT_FILE" --relative-to "$ROOT" 2>/dev/null)
    if [ -z "$CTX_REF" ] || [ "${CTX_REF#../}" != "$CTX_REF" ]; then
        CTX_REF=$("$CC_PYTHON" "$SELF_DIR/handoff.py" path --path "$CONTEXT_FILE" 2>/dev/null || printf '%s' "$CONTEXT_FILE")
    fi
    if [ "$_got_positional" -eq 0 ]; then
        INJECT="Your context was just cleared by gclear. Read $CTX_REF immediately and continue according to its contents."
    elif printf '%s' "$INJECT_RAW" | grep -qF "$CONTEXT_BASENAME"; then
        INJECT="$INJECT_RAW"
    else
        INJECT="First read $CTX_REF, then: $INJECT_RAW"
        NOTICE="Automatically added a reference to $CONTEXT_BASENAME (the original prompt lacked that keyword)"; log "NOTICE: $NOTICE"
    fi
else
    if [ "$_got_positional" -eq 0 ]; then INJECT="Your context was just cleared by gclear. Please continue the previous work."; else INJECT="$INJECT_RAW"; fi
fi
INJECT="$(printf '%s' "$INJECT" | tr '\n\r' '  ')"
MARK_TOKEN="$(head -c4 /dev/urandom 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n')"
[ -z "$MARK_TOKEN" ] && MARK_TOKEN="$(date +%s)$$"
CONFIRM_MARKER="[[gclear:${MARK_TOKEN}]]"
INJECT="${INJECT} ${CONFIRM_MARKER}"
log "INJECT=$INJECT"

MODEL="unknown model"
VER=$("$CC_PYTHON" "$SELF_DIR/handoff.py" run --timeout 5 -- claude --version 2>/dev/null | head -1 || echo "unknown version")

# ── ledger ────────────────────────────────────────────────────────────────
write_ledger() {
    printf '%s | root=%s | old_sid=%s | new_sid=%s | clear_receipt=%s | inject_receipt=%s | submit_ts=%s | marker=%s\n' \
        "$(date '+%F %T')" "$ROOT" "${OLD_SID:-}" "${CC_NEW_SID:-${new_sid:-}}" \
        "${CLEAR_RESULT:-not-attempted}" "${INJECT_RESULT:-not-attempted}" "${SUBMIT_TS:-}" "$CONFIRM_MARKER" >> "$LEDGER"
    printf '%s | status=%s | mode=%s | no_file=%s | clear_only=%s | context=%s | model=%s | model_result=%s | inject_raw=%s | inject_final=%s | notice=%s | verify=%s | submit=%s | notify=%s\n' \
        "$(date '+%F %T')" "$STATUS" "${DRIVER:-unknown}" "$NO_FILE" "$CLEAR_ONLY" "${CONTEXT_FILE:-<none>}" \
        "${MODEL_ARG:-none}" "$MODEL_RESULT" "${INJECT_RAW:-<default>}" "$INJECT" "${NOTICE:-none}" \
        "$VERIFIED_BY" "$SUBMIT_RESULT" "$NOTIFY_SENT" >> "$LEDGER" 2>/dev/null
}
trap write_ledger EXIT

# ── deadline: external preserved; normal gets an internal budget ─────────
if [ -n "$GCLEAR_EXTERNAL_DEADLINE" ]; then
    export GCLEAR_DEADLINE="$GCLEAR_EXTERNAL_DEADLINE"
else
    export GCLEAR_DEADLINE=$(($(date +%s) + GCLEAR_TOTAL_BUDGET))
fi
[ -n "$GCLEAR_EXTERNAL_OPERATION" ] && export GCLEAR_OPERATION="$GCLEAR_EXTERNAL_OPERATION"
handoff_init || { STATUS='fail(operation-guard)'; log "operation guard rejected the run"; exit 1; }

# ════════════════════ identity: caller anchor first ════════════════════
_finish() {  # <rc>
    if [ "$STATUS" = success ]; then
        notify "gclear confirmed a new SID and a real user submission | mode=$DRIVER | old=$OLD_SID new=${CC_NEW_SID:-${new_sid:-}} | model ${MODEL} | ${VER}${MODEL_WARN:-}"
    else
        notify "gclear did not confirm the hand-off: $STATUS | mode=${DRIVER:-unknown} old=${OLD_SID:-?} new=${CC_NEW_SID:-${new_sid:-?}} | active operations stopped; an unknown result must not be re-injected on its basis."
    fi
    exit "$1"
}

_caller=$(gclear_caller)
DRIVER=""; CALLER_SID=""; CALLER_PID=""
if [ -n "$_caller" ]; then
    read -r DRIVER CALLER_SID CALLER_PID <<< "$_caller"
fi
# ── v1.2.3 mechanical guards (synchronous, zero new polling) ─────────────
# Rule 1: executor context must never clear sessions. D-backend executors get
# QGO_EXECUTOR_ID injected at spawn; session lifecycle is owned by the
# orchestrator/driver, not the executor. Applies regardless of tmux.
if [ -n "${QGO_EXECUTOR_ID:-}" ]; then
    STATUS='fail(executor-context)'
    log "ERROR: QGO_EXECUTOR_ID=${QGO_EXECUTOR_ID} set — executor context may not run gclear; refusing"
    _finish 2
fi
# Rule 2: anchor missed AND no tmux pane = bare CLI (no injection channel by
# design). Never fall into scoped legacy detection here: a live same-project
# daemon would be misjudged as "cc" and the WRONG session cleared.
if [ -z "$DRIVER" ] && [ -z "${TMUX_PANE:-}" ]; then
    STATUS='fail(no-driver)'
    log "ERROR: caller anchor missed and \$TMUX_PANE empty — bare CLI has no handoff channel; refusing (use CC daemon mode or tmux CLI mode)"
    _finish 2
fi

if [ -z "$DRIVER" ]; then
    log "WARN: caller anchor unavailable (CLAUDE_CODE_SESSION_ID/pid map/pane); using legacy fail-closed detection"
    if _legacy=$(handoff_run detect_driver); then
        DRIVER="$_legacy"
    else
        STATUS='fail(no-driver)'
        log "ERROR: cannot determine session driver; refusing to act"
        _finish 2
    fi
fi
log "driver=$DRIVER caller_sid=${CALLER_SID:-?} caller_pid=${CALLER_PID:-?} supervised=${GCLEAR_SUPERVISED}"

# ════════════════════ CC adapter ════════════════════
if [ "$DRIVER" = "cc" ] || [ "$DRIVER" = "cc-connect" ]; then
    STATUS='fail(no-project)'
    CC_PROJECT=$(handoff_run cc_project_for_dir "$ROOT") || { _finish 2; }
    [ -n "$CC_PROJECT" ] || { _finish 2; }
    CC_SKEY=""
    if [ -n "${CC_SESSION_KEY:-}" ] && handoff_run __sd_key_in_project_sessions "$CC_PROJECT" "$CC_SESSION_KEY"; then
        CC_SKEY="$CC_SESSION_KEY"; log "cc self key adopted: $CC_SKEY"
    else
        CC_SKEY=$(handoff_run cc_session_key "$CC_PROJECT") || { STATUS='fail(no-session)'; _finish 2; }
    fi
    [ -n "$CC_SKEY" ] || { STATUS='fail(no-session)'; _finish 2; }

    # Supervised truth wins; otherwise the caller's own session is authoritative.
    if [ "$GCLEAR_SUPERVISED" = 1 ] && [ -n "$GCLEAR_EXTERNAL_OPERATION" ]; then
        _op_sid=$("$CC_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("old_sid") or "")' "$GCLEAR_EXTERNAL_OPERATION" 2>/dev/null)
        if [ -n "$_op_sid" ]; then
            [ -n "$CALLER_SID" ] && [ "$CALLER_SID" != "$_op_sid" ] && log "WARN: caller SID != operation old_sid; operation wins"
            OLD_SID="$_op_sid"
        fi
    fi
    [ -n "$OLD_SID" ] || OLD_SID="$CALLER_SID"
    [ -n "$OLD_SID" ] || OLD_SID=$(handoff_run cc_agent_sid "$CC_PROJECT" "$CC_SKEY" 2>/dev/null || true)
    [ -n "$OLD_SID" ] || { STATUS='fail(no-session)'; _finish 2; }

    if cc_handoff; then
        _finish 0
    else
        _finish 1
    fi
fi

# ════════════════════ CLI adapter ════════════════════
if [ -z "${TMUX_PANE:-}" ] || ! command -v tmux >/dev/null 2>&1; then
    STATUS='fail(no-tmux)'
    log "ERROR: not in tmux (\$TMUX_PANE empty or tmux missing); refusing to act"
    _finish 2
fi
TARGET="$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null)"
if [ -z "$TARGET" ]; then STATUS='fail(no-target)'; log "ERROR: cannot resolve tmux target"; _finish 2; fi
SESSION="${TARGET%%:*}"
tmux has-session -t "$SESSION" 2>/dev/null || { STATUS='fail(no-target)'; _finish 2; }
log "target=$TARGET (TMUX_PANE=$TMUX_PANE) no_file=$NO_FILE context=${CONTEXT_FILE:-<none>}"

BUSY_RE='esc to interrupt|\([0-9][^)]*·[^)]*token'
FEEDBACK_RE='How is Claude doing this session|1:\s*Bad.*?2:\s*Fine.*?3:\s*Good'
TRUST_RE='Do you trust the files in this folder\?'
cap() { handoff_run tmux capture-pane -t "$TARGET" -p 2>/dev/null; }
pane_tail() { cap | awk '{l[NR]=$0} END{e=NR; while(e>0 && l[e] ~ /^[[:space:]]*$/) e--; s=e-11; if(s<1)s=1; for(i=s;i<=e;i++) print l[i]}'; }
classify_pane() {
    local view
    view=$(pane_tail) || { printf 'unknown\n'; return 1; }
    [ -n "$view" ] || { printf 'unknown\n'; return 1; }
    printf '%s' "$view" | BUSY_RE="$BUSY_RE" FEEDBACK_RE="$FEEDBACK_RE" TRUST_RE="$TRUST_RE" "$CC_PYTHON" -c '
import sys, os, re
t = sys.stdin.read()
if re.search(os.environ["BUSY_RE"], t): print("busy")
elif re.search(os.environ["FEEDBACK_RE"], t, re.S): print("overlay_feedback")
elif re.search(os.environ["TRUST_RE"], t): print("overlay_trust")
else: print("ready")
' 2>/dev/null
}

handle_overlay() {
    local k
    for k in 1 2 3; do
        log "detected feedback rating overlay, attempting to close (attempt $k)"
        cli_expect_sid "${new_sid:-$OLD_SID}"
        handoff_run tmux send-keys -t "$TARGET" Escape 2>/dev/null; handoff_sleep 1
        [ "$(classify_pane)" != "overlay_feedback" ] && { log "overlay closed"; return 0; }
        cli_expect_sid "${new_sid:-$OLD_SID}"
        handoff_run tmux send-keys -t "$TARGET" "0" 2>/dev/null; handoff_sleep 1
        [ "$(classify_pane)" != "overlay_feedback" ] && { log "overlay closed"; return 0; }
    done
    STATUS="fail(overlay)"
    gclear_fallback overlay
    return 1
}

cli_wait_ready() {                  # consecutive ready pane readings
    local hits=0 waited=0 st
    while [ "$waited" -lt 30 ]; do
        st="$(classify_pane)"
        case "$st" in
            ready) hits=$((hits + 1)); [ "$hits" -ge 3 ] && return 0 ;;
            busy)  hits=0 ;;
            unknown|'') STATUS='fail(pane-unreadable)'; return 1 ;;
            overlay_feedback) hits=0; handle_overlay || return 1 ;;
            overlay_trust) STATUS='fail(trust)'; log "trust dialog; refusing"; return 1 ;;
        esac
        handoff_sleep 2 || return 1; waited=$((waited + 2))
    done
    STATUS='fail(idle-unconfirmed)'; return 1
}

cli_expect_sid() {                  # <expected>
    local snap current
    handoff_guard || exit 1
    snap=$(gclear_pane "$TARGET") || snap=""
    current="${snap%%$'\t'*}"
    [ -n "$current" ] && [ "$current" = "$1" ] && return 0
    STATUS='fail(session-changed)'; log "ERROR: trusted pane SID changed or unavailable"; exit 1
}
xcontext_model() { gclear_pane_model "$TARGET"; }
xcontext_snapshot() { gclear_pane "$TARGET"; }

do_send() {                         # <text>
    local expected="${new_sid:-$OLD_SID}"
    SUBMIT_TS=$(gclear_stamp)
    handoff_receipt "${SEND_PHASE:-clear}" "$expected" || return 1
    cli_expect_sid "$expected"
    handoff_run tmux send-keys -t "$TARGET" Escape 2>/dev/null || return 1
    cli_expect_sid "$expected"
    handoff_run tmux send-keys -t "$TARGET" C-u 2>/dev/null || return 1
    printf '%s' "$1" | tmux load-buffer -b gclear - 2>/dev/null || return 1
    cli_expect_sid "$expected"
    handoff_run tmux paste-buffer -t "$TARGET" -b gclear -d 2>/dev/null || return 1
    cli_expect_sid "$expected"
    handoff_guard || return 1
    handoff_run tmux send-keys -t "$TARGET" Enter 2>/dev/null
}

# resolve OLD_SID from the caller anchor / pane
[ -n "$OLD_SID" ] || OLD_SID="$CALLER_SID"
if [ -z "$OLD_SID" ]; then
    _snap=$(gclear_pane "$TARGET") || _snap=""
    OLD_SID="${_snap%%$'\t'*}"
fi
[ -n "$OLD_SID" ] || { STATUS='fail(no-trusted-sid)'; log "no trusted SID"; _finish 1; }
_m0=$(xcontext_model); [ -n "$_m0" ] && MODEL="$_m0"
log "before clear OLD_SID=$OLD_SID model=$MODEL"

if [ "$RESUME_INJECT" = 1 ]; then
    # Inject only; no clear.
    new_sid="$OLD_SID"
    SEND_PHASE=inject
    cli_sleep_ok() { handoff_sleep "$1" || exit 1; }
    cli_sleep_ok 1
    cli_expect_sid "$new_sid"
    tmux set-buffer -b gclear_inject "$INJECT" 2>/dev/null || { STATUS='fail(buffer)'; _finish 1; }
    cli_expect_sid "$new_sid"
    handoff_run tmux paste-buffer -t "$TARGET" -b gclear_inject -d -p 2>/dev/null || { STATUS='fail(paste)'; _finish 1; }
    cli_sleep_ok 1
    cli_expect_sid "$new_sid"
    SUBMIT_TS=$(gclear_stamp)
    INJECT_RESULT=unknown
    handoff_receipt inject "$new_sid" || _finish 1
    handoff_guard || _finish 1
    handoff_run tmux send-keys -t "$TARGET" Enter 2>/dev/null || true
    _until=$(( $(date +%s) + GCLEAR_INJECT_CONFIRM )); SUBMIT_OK=0
    while [ "$(date +%s)" -lt "$_until" ]; do
        cli_expect_sid "$new_sid"
        if handoff_marker "$new_sid"; then SUBMIT_OK=1; break; fi
        handoff_sleep "$GCLEAR_POLL_INTERVAL" || _finish 1
    done
    [ "$SUBMIT_OK" = 1 ] || { INJECT_RESULT=unconfirmed; STATUS='fail(submit-unconfirmed)'; gclear_fallback submit-unconfirmed; _finish 1; }
    INJECT_RESULT=accepted; SUBMIT_RESULT='ok(0retry)'; STATUS=success; VERIFIED_BY=transcript
    _finish 0
fi

# 1) idle gate
if ! gclear_wait_idle "$OLD_SID" "$GCLEAR_BUSY_TIMEOUT" "$GCLEAR_IDLE_STABLE"; then
    local_attempt=0
    while [ "$local_attempt" -lt "${GCLEAR_INTERRUPT_ATTEMPTS:-0}" ]; do
        [ "$(gclear_idle "$OLD_SID")" = idle ] && break
        log "interrupt attempt (Escape)"
        handoff_run tmux send-keys -t "$TARGET" Escape 2>/dev/null
        local_attempt=$((local_attempt + 1))
        handoff_sleep "$GCLEAR_INTERRUPT_GRACE" || _finish 1
    done
    [ "$(gclear_idle "$OLD_SID")" = idle ] || { STATUS='fail(busy-timeout)'; gclear_fallback busy-timeout; _finish 1; }
fi
cli_wait_ready || _finish 1
gclear_test_pause GCLEAR_PAUSE_AFTER_IDLE || _finish 1

# 2) clear + staged confirmation
CC_CLEAR_TS=$(gclear_stamp)
SEND_PHASE=clear
do_send /clear || true
CLEAR_RESULT=unknown
_new=""; _seen_new=0; _resends=0
_confirm_until=$(( $(date +%s) + GCLEAR_CLEAR_CONFIRM ))
_late_until=$(( _confirm_until + GCLEAR_CLEAR_LATE ))
while [ "$(date +%s)" -lt "$_late_until" ]; do
    handoff_guard || _finish 1
    _snap=$(gclear_pane "$TARGET") || _snap=""
    _cur="${_snap%%$'\t'*}"
    if [ -n "$_cur" ] && [ "$_cur" != "$OLD_SID" ] && [ "$(gclear_has_clear "$_cur" "$CC_CLEAR_TS")" = 1 ]; then
        _new="$_cur"; break
    fi
    if [ -n "$_cur" ] && [ "$_cur" != "$OLD_SID" ]; then _seen_new=1; fi
    _disc=$(gclear_discover "$OLD_SID" "$CC_CLEAR_TS")
    case "$_disc" in
        ok\ *) _cand="${_disc#ok }"
               [ "$(gclear_has_clear "$_cand" "$CC_CLEAR_TS")" = 1 ] && { _new="$_cand"; break; }
               _seen_new=1 ;;
        ambiguous\ *) STATUS='fail(clear-ambiguous)'; log "ambiguous: ${_disc#ambiguous }"; gclear_fallback clear-ambiguous; _finish 1 ;;
    esac
    if [ "$(date +%s)" -ge "$_confirm_until" ] && [ "$_resends" -lt "$GCLEAR_CLEAR_RETRIES" ] && [ "$_seen_new" = 0 ]; then
        if [ "$(gclear_idle "$OLD_SID")" = idle ]; then
            log "clear unconfirmed and idle; one safe resend"
            do_send /clear || true
            _resends=$((_resends + 1))
        fi
    fi
    handoff_sleep "$GCLEAR_POLL_INTERVAL" || _finish 1
done
[ -n "$_new" ] || { STATUS='fail(clear-unconfirmed)'; log "clear unconfirmed"; gclear_fallback clear-unconfirmed; _finish 1; }
new_sid="$_new"; CLEAR_RESULT=accepted; VERIFIED_BY=transcript
log "clear confirmed new_sid=$new_sid"
if [ "$CLEAR_ONLY" -eq 1 ]; then
    SUBMIT_RESULT='clear-only'; STATUS=success
    log "clear-only done"
    _finish 0
fi

# 3) optional model switch
if [ -n "$MODEL_ARG" ]; then
    cli_expect_sid "$new_sid"
    SEND_PHASE=model
    do_send "/model $MODEL_ARG" || { STATUS='fail(model-unconfirmed)'; _finish 1; }
    handoff_sleep 2 || _finish 1
    cli_wait_ready || _finish 1
fi

# 4) inject
log "injecting prompt"
handoff_sleep 1 || _finish 1
cli_expect_sid "$new_sid"
tmux set-buffer -b gclear_inject "$INJECT" 2>/dev/null || { STATUS='fail(buffer)'; _finish 1; }
cli_expect_sid "$new_sid"
handoff_run tmux paste-buffer -t "$TARGET" -b gclear_inject -d -p 2>/dev/null || { STATUS='fail(paste)'; _finish 1; }
handoff_sleep 1 || _finish 1
cli_expect_sid "$new_sid"
SUBMIT_TS=$(gclear_stamp)
INJECT_RESULT=unknown
handoff_receipt inject "$new_sid" || _finish 1
handoff_guard || _finish 1
handoff_run tmux send-keys -t "$TARGET" Enter 2>/dev/null || true

_until=$(( $(date +%s) + GCLEAR_INJECT_CONFIRM )); SUBMIT_OK=0
while [ "$(date +%s)" -lt "$_until" ]; do
    cli_expect_sid "$new_sid"
    if handoff_marker "$new_sid"; then SUBMIT_OK=1; break; fi
    handoff_sleep "$GCLEAR_POLL_INTERVAL" || _finish 1
done
[ "$SUBMIT_OK" = 1 ] || { INJECT_RESULT=unconfirmed; STATUS='fail(submit-unconfirmed)'; gclear_fallback submit-unconfirmed; _finish 1; }
INJECT_RESULT=accepted; SUBMIT_RESULT='ok(0retry)'; STATUS=success

# 5) success notify (model verification)
_cur_model=$(xcontext_model)
MODEL_WARN=""; MODEL_NOTE=""
if [ -n "$MODEL_ARG" ]; then
    _target_id=$(model_resolve "$MODEL_ARG")
    if [ -z "$_target_id" ]; then
        [ -n "$_cur_model" ] && MODEL="$_cur_model"; MODEL_RESULT="dynamic"; MODEL_NOTE="attempted to switch to ${MODEL_ARG} (dynamic target)"
    elif [ -z "$_cur_model" ]; then
        MODEL="$_target_id"; MODEL_RESULT="ok(unverified)"
    elif [ "$_cur_model" != "$_target_id" ]; then
        MODEL="$_cur_model"; MODEL_RESULT="mismatch"; MODEL_WARN="⚠️ model did not switch to ${MODEL_ARG} (current ${_cur_model})"
    else
        MODEL="$_cur_model"; MODEL_RESULT="ok"
    fi
else
    [ -n "$_cur_model" ] && MODEL="$_cur_model"
fi
log "done. status=$STATUS verify=$VERIFIED_BY submit=$SUBMIT_RESULT notify=$NOTIFY_SENT model_result=$MODEL_RESULT"
_finish 0
