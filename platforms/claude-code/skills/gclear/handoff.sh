#!/bin/bash
# gclear 1.2.4 handoff policy layer (shared kernel + CC adapter).
# The vendored driver stays a clean copy of the canonical session driver; every
# correctness guarantee (idle gate, staged retry, rc contract, fallback) is
# enforced here instead of trusting the driver's accepted/receipt semantics.

SUBMIT_TS="${SUBMIT_TS:-}"
WSTMP="${WSTMP:-${TMPDIR:-/tmp}}"

gclear_python() {
    local candidate env_python=""
    if [ -r "$ROOT/.env" ]; then
        # POSIX-safe quoting: BSD sed does not understand '\x27'.
        env_python=$(sed -nE 's/^[A-Za-z0-9_]+_PYTHON=["'"'"']?([^"'"'"']*)["'"'"']?[[:space:]]*$/\1/p' "$ROOT/.env" | head -1)
    fi
    for candidate in "${CC_PYTHON:-}" "${__SD_PYTHON_RESOLVED:-}" "$env_python" "$(command -v python3)" python3; do
        [ -n "$candidate" ] || continue
        if "$candidate" -c 'import sys, tomllib; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"; return 0
        fi
    done
    return 1
}

handoff_marker() {
    "$CC_PYTHON" "$SELF_DIR/handoff.py" marker "$ROOT" "$1" "$CONFIRM_MARKER" "$SUBMIT_TS"
}

handoff_init() {
    local budget
    budget=$(handoff_budget) || return 1
    HANDOFF_SECONDS_DEADLINE=$((SECONDS + budget))
    export GCLEAR_DEADLINE ROOT SELF_DIR WSTMP CC_PYTHON HANDOFF_SECONDS_DEADLINE
    local _ _ _name
    while read -r _ _ _name; do export -f "$_name"; done < <(declare -F)
}

handoff_budget() {
    local value
    value=$("$CC_PYTHON" "$SELF_DIR/handoff.py" budget "$ROOT" "$GCLEAR_DEADLINE" "${GCLEAR_OPERATION:-}") || return 1
    value=${value%%.*}
    # Supervised phase cap is an INTERNAL planning bound only. The external
    # GCLEAR_DEADLINE and the operation journal stay untouched.
    if [ "${GCLEAR_SUPERVISED:-0}" = 1 ] && [ -n "${GCLEAR_SUPERVISED_PHASE_CAP:-}" ] \
       && [ "$value" -gt "$GCLEAR_SUPERVISED_PHASE_CAP" ]; then
        value="$GCLEAR_SUPERVISED_PHASE_CAP"
    fi
    if [ -n "${HANDOFF_SECONDS_DEADLINE:-}" ] && [ "$((HANDOFF_SECONDS_DEADLINE - SECONDS))" -lt "$value" ]; then
        value=$((HANDOFF_SECONDS_DEADLINE - SECONDS))
    fi
    [ "$value" -gt 0 ] || return 1
    printf '%s\n' "$value"
}

handoff_guard() {
    handoff_budget >/dev/null || { STATUS='fail(cancelled-or-deadline)'; return 1; }
}

handoff_sleep() {
    local budget delay="$1"
    budget=$(handoff_budget) || return 1
    [ "$delay" -le "$budget" ] || delay=$budget
    sleep "$delay"
    handoff_guard
}

handoff_run() {
    local budget
    export CONFIRM_MARKER SUBMIT_TS CC_PROJECT CC_SKEY
    budget=$(handoff_budget) || return 1
    "$CC_PYTHON" "$SELF_DIR/handoff.py" run --timeout "$budget" -- \
        bash -c '"$@"' handoff-call "$@"
}

handoff_receipt() {
    "$CC_PYTHON" "$SELF_DIR/handoff.py" receipt "$ROOT" "$GCLEAR_DEADLINE" "${GCLEAR_OPERATION:-}" \
        "$1" "$2" "${SUBMIT_TS:-}" "${CLEAR_RESULT:-not-attempted}" "${INJECT_RESULT:-not-attempted}"
}

# ── fallback: durable JSON + guidance + notification ─────────────────────
# Never claims remote cancellation; always leaves a recoverable path.
gclear_fallback() {                  # <reason>
    local reason="$1" dir="$SELF_DIR/.operations" path token
    token="${MARK_TOKEN:-$RANDOM$RANDOM}"
    mkdir -p "$dir" 2>/dev/null; chmod 700 "$dir" 2>/dev/null
    path="$dir/fallback-${token}.json"
    GCLEAR_FB_REASON="$reason" GCLEAR_FB_PATH="$path" \
    GCLEAR_FB_OLD="${OLD_SID:-}" GCLEAR_FB_NEW="${CC_NEW_SID:-${new_sid:-}}" \
    GCLEAR_FB_MODE="${DRIVER:-unknown}" GCLEAR_FB_PROMPT="${INJECT:-}" \
    GCLEAR_FB_STATUS="${STATUS:-unknown}" GCLEAR_FB_MARKER="${CONFIRM_MARKER:-}" \
    CLEAR_RESULT="${CLEAR_RESULT:-not-attempted}" INJECT_RESULT="${INJECT_RESULT:-not-attempted}" \
    "$CC_PYTHON" - "$ROOT" <<'PY'
import json, os, sys
root = sys.argv[1]
prompt = os.environ.get('GCLEAR_FB_PROMPT', '')
old = os.environ.get('GCLEAR_FB_OLD', '')
mode = os.environ.get('GCLEAR_FB_MODE', 'unknown')
recovery = (
    "cd %s\n"
    "# 1) stop this project's cc daemon (use the project's normal stop command)\n"
    "# 2) seed the session store back to the old SID: if the host provides a cc\n"
    "#    session-switch skill, follow its documented SID-restore method to set\n"
    "#    agent_session_id = %s; otherwise resume the session and verify\n"
    "# 3) restart/resume the session\n"
    "# 4) verify the store agent_session_id == %s\n"
    "# 5) resume the handoff WITHOUT clearing again:\n"
    "#    bash .claude/skills/gclear/gclear.sh --resume-inject %s"
) % (root, old or '<OLD_SID>', old or '<OLD_SID>', json.dumps(prompt))
record = dict(
    created=__import__('datetime').datetime.now().isoformat(timespec='seconds'),
    reason=os.environ.get('GCLEAR_FB_REASON', ''),
    status=os.environ.get('GCLEAR_FB_STATUS', ''),
    mode=mode,
    old_sid=old,
    new_sid=os.environ.get('GCLEAR_FB_NEW', ''),
    marker=os.environ.get('GCLEAR_FB_MARKER', ''),
    prompt=prompt,
    clear_receipt=os.environ.get('CLEAR_RESULT', ''),
    inject_receipt=os.environ.get('INJECT_RESULT', ''),
    guidance=("Do not repeat /clear; verify the marker and the target session first. If /clear has "
              "already taken effect, use --resume-inject to inject the prompt only."),
    recovery=recovery,
)
fd = os.open(os.environ['GCLEAR_FB_PATH'], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(record, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
PY
    STATUS="${STATUS:-fail($reason)}"
    log "fallback written: $path ($reason)"
    if [ "${GCLEAR_NOTIFY_FALLBACK:-1}" = 1 ]; then
        notify "gclear did not complete ($reason) | mode=$DRIVER old_sid=${OLD_SID:-?} new_sid=${CC_NEW_SID:-${new_sid:-?}} | fallback file written: $path | do not repeat /clear; follow the recovery command in the file, or use --resume-inject to inject the prompt only."
    fi
}

# ── CC send with the strict rc contract ─────────────────────────────────
# 0 accepted -> no resend. 1 transport/unknown -> no auto retry.
# 2 -> retry ONLY on an explicit `session busy`; other async errors never retry.
# 3 addressing -> fatal.
cc_checked_send() {                  # <message> <expected_sid> <phase> [busy_retries]
    local message="$1" expected="$2" phase="$3" retries="${4:-0}"
    local attempts=0 current rc err
    CC_SEND_RESULT=unknown
    err=$(mktemp "$WSTMP/send.XXXXXX") || return 1
    while :; do
        handoff_guard || { CC_SEND_RESULT=cancelled; rm -f "$err"; return 1; }
        current=$(handoff_run cc_agent_sid "$CC_PROJECT" "$CC_SKEY") || current=""
        handoff_guard || { CC_SEND_RESULT=cancelled; rm -f "$err"; return 1; }
        if [ -z "$current" ] || [ "$current" != "$expected" ]; then
            CC_SEND_RESULT=session-changed; rm -f "$err"; return 3
        fi
        SUBMIT_TS=$(gclear_stamp)
        case "$phase" in clear) CLEAR_RESULT=unknown ;; inject) INJECT_RESULT=unknown ;; esac
        handoff_receipt "$phase" "$expected" || { CC_SEND_RESULT=cancelled; rm -f "$err"; return 1; }
        handoff_guard || { CC_SEND_RESULT=cancelled; rm -f "$err"; return 1; }
        if handoff_run cc_inject "$CC_PROJECT" "$CC_SKEY" "$message" > /dev/null 2>"$err"; then
            CC_SEND_RESULT=accepted; rm -f "$err"; return 0
        else
            rc=$?
        fi
        case "$rc" in
            3) CC_SEND_RESULT=rejected; rm -f "$err"; return 3 ;;
            1) CC_SEND_RESULT=unknown; rm -f "$err"; return 1 ;;
            2)
                if grep -q '^cc_inject: session busy:' "$err"; then
                    if [ "$attempts" -ge "$retries" ]; then
                        CC_SEND_RESULT=busy-timeout; rm -f "$err"; return 2
                    fi
                    attempts=$((attempts + 1))
                    log "session busy; retry $attempts/$retries"
                    handoff_sleep "$GCLEAR_POLL_INTERVAL" || { CC_SEND_RESULT=cancelled; rm -f "$err"; return 1; }
                    continue
                fi
                CC_SEND_RESULT=async-error; rm -f "$err"; return 2 ;;
            *) CC_SEND_RESULT=unknown; rm -f "$err"; return 1 ;;
        esac
    done
}

# ── CC interrupt ────────────────────────────────────────────────────────
# Phase 0 proves which channel (if any) actually cancels a turn. The platform
# commands /esc and /stop are message handlers, not injectable slash commands,
# so they are NOT used here. Only an explicitly proven command is honoured.
cc_interrupt() {
    [ "${GCLEAR_INTERRUPT_ENABLE:-0}" = 1 ] || return 1
    [ -n "${GCLEAR_INTERRUPT_CMD:-}" ] || return 1
    log "interrupt attempt via configured channel"
    CC_PROJECT="$CC_PROJECT" CC_SKEY="$CC_SKEY" ROOT="$ROOT" \
        handoff_run bash -c "$GCLEAR_INTERRUPT_CMD"
}

# ── clear confirmation: unique new session + /clear command entry (hard) ──
# Sets CC_NEW_SID on success. Never resends clear once a new session exists.
cc_confirm_clear() {                 # <old_sid> <clear_ts> <resume_ts>
    local old="$1" clear_ts="$2" resume_ts="$3"
    local confirm_until=$(( $(date +%s) + GCLEAR_CLEAR_CONFIRM ))
    local late_until=$(( confirm_until + GCLEAR_CLEAR_LATE ))
    local resends=0 candidate disc seen_new=0 now
    CC_NEW_SID=""
    while :; do
        handoff_guard || return 1
        now=$(date +%s)
        disc=$(gclear_discover "$old" "$clear_ts")
        case "$disc" in
            ok\ *)
                candidate="${disc#ok }"
                if [ "$(gclear_has_clear "$candidate" "$clear_ts")" = 1 ]; then
                    CC_NEW_SID="$candidate"
                    log "clear confirmed: new_sid=$candidate (/clear entry)"
                    return 0
                fi
                seen_new=1 ;;
            ambiguous\ *)
                STATUS='fail(clear-ambiguous)'
                log "clear ambiguous candidates: ${disc#ambiguous }"
                gclear_fallback clear-ambiguous
                return 1 ;;
            none) ;;
        esac
        [ "$now" -lt "$late_until" ] || break
        if [ "$now" -ge "$confirm_until" ] && [ "$resends" -lt "$GCLEAR_CLEAR_RETRIES" ] && [ "$seen_new" = 0 ]; then
            # Safe single resend ONLY when: no new session, no /clear entry, idle.
            if [ "$(gclear_idle "$old")" = idle ] && [ "$(gclear_has_clear "$old" "$clear_ts")" != 1 ]; then
                log "clear not confirmed and still idle; one safe resend"
                cc_checked_send /clear "$old" clear 0 || true
                resends=$((resends + 1))
                CLEAR_RESULT="$CC_SEND_RESULT"
            fi
        fi
        handoff_sleep "$GCLEAR_POLL_INTERVAL" || return 1
    done
    STATUS='fail(clear-unconfirmed)'
    log "clear unconfirmed (new session seen=$seen_new)"
    gclear_fallback clear-unconfirmed
    return 1
}

cc_marker_wait() {                   # <sid> <seconds>
    local sid="$1" limit="$2" until now
    until=$(( $(date +%s) + limit ))
    while :; do
        handoff_guard || return 1
        # Misdelivery: the marker must never land in the old session.
        if [ "$sid" != "$OLD_SID" ] && handoff_run handoff_marker "$OLD_SID"; then
            STATUS='fail(misdelivery)'
            log "marker landed in old session"
            gclear_fallback misdelivery
            return 1
        fi
        if handoff_marker "$sid"; then
            return 0
        fi
        now=$(date +%s)
        [ "$now" -lt "$until" ] || return 1
        handoff_sleep "$GCLEAR_POLL_INTERVAL" || return 1
    done
}

# ── CC adapter main flow ────────────────────────────────────────────────
# Preconditions set by gclear.sh: ROOT/SELF_DIR, config, CC_PROJECT, CC_SKEY,
# OLD_SID (authoritative from caller or supervised operation record).
cc_handoff() {
    handoff_init || { STATUS='fail(operation-guard)'; return 1; }
    STATUS='fail(no-project)'
    [ -n "${CC_PROJECT:-}" ] || return 2
    [ -n "${CC_SKEY:-}" ] || return 2
    [ -n "${OLD_SID:-}" ] || return 2
    if ! "$CC_PYTHON" - "$CC_CONNECT_CONFIG" "$CC_PROJECT" "$ROOT" <<'PY'
import pathlib, sys, tomllib
with open(sys.argv[1], 'rb') as stream:
    rows = [p for p in tomllib.load(stream).get('projects', []) if p.get('name') == sys.argv[2]]
assert len(rows) == 1
assert rows[0].get('inject_sender') in (None, False)
assert pathlib.Path(rows[0]['agent']['options']['work_dir']).resolve() == pathlib.Path(sys.argv[3]).resolve()
PY
    then
        STATUS='fail(command-channel)'; return 2
    fi

    if [ "${RESUME_INJECT:-0}" = 1 ]; then
        # Skip clear entirely; only (re)deliver the prompt into the bound session.
        cc_resume_inject
        return $?
    fi

    # 1) idle gate (transcript primary)
    if ! gclear_wait_idle "$OLD_SID" "$GCLEAR_BUSY_TIMEOUT" "$GCLEAR_IDLE_STABLE"; then
        local attempt=0
        while [ "$attempt" -lt "${GCLEAR_INTERRUPT_ATTEMPTS:-0}" ]; do
            if [ "$(gclear_idle "$OLD_SID")" = idle ]; then break; fi
            cc_interrupt || break
            attempt=$((attempt + 1))
            handoff_sleep "$GCLEAR_INTERRUPT_GRACE" || return 1
        done
        if [ "$(gclear_idle "$OLD_SID")" != idle ]; then
            STATUS='fail(busy-timeout)'
            log "still busy after ${GCLEAR_BUSY_TIMEOUT}s; refusing to clear"
            gclear_fallback busy-timeout
            return 1
        fi
        log "re-check: session is idle (was a false-busy reading)"
    fi
    gclear_test_pause GCLEAR_PAUSE_AFTER_IDLE || return 1

    # 2) clear (send once, observe; resend only under the safe conditions)
    CC_CLEAR_TS=$(gclear_stamp)
    cc_checked_send /clear "$OLD_SID" clear "$GCLEAR_CLEAR_RETRIES" || true
    CLEAR_RESULT="$CC_SEND_RESULT"
    handoff_receipt clear "$OLD_SID" || return 1
    log "clear_receipt=$CLEAR_RESULT old_sid=$OLD_SID"
    case "$CLEAR_RESULT" in
        accepted|unknown) ;;
        *) STATUS="fail(clear-$CLEAR_RESULT)"; gclear_fallback "clear-$CLEAR_RESULT"; return 1 ;;
    esac
    cc_confirm_clear "$OLD_SID" "$CC_CLEAR_TS" "$SUBMIT_TS" || return 1
    if [ "${CLEAR_ONLY:-0}" = 1 ]; then
        SUBMIT_RESULT='clear-only'; STATUS=success; VERIFIED_BY=cc-api
        return 0
    fi

    # 3) optional model switch
    if [ -n "${MODEL_ARG:-}" ] && [ "${GCLEAR_MODEL_CONFIRM:-0}" -gt 0 ]; then
        cc_checked_send "/model $MODEL_ARG" "$CC_NEW_SID" model "$GCLEAR_MODEL_RETRIES" || true
        MODEL_RESULT="$CC_SEND_RESULT"
        if [ "$CC_SEND_RESULT" != accepted ]; then
            STATUS='fail(model-unconfirmed)'; gclear_fallback model-unconfirmed; return 1
        fi
    fi

    # 4) inject
    cc_checked_send "$INJECT" "$CC_NEW_SID" inject "$GCLEAR_INJECT_RETRIES" || true
    INJECT_RESULT="$CC_SEND_RESULT"
    handoff_receipt inject "$CC_NEW_SID" || return 1
    log "inject_receipt=$INJECT_RESULT new_sid=$CC_NEW_SID submit_ts=$SUBMIT_TS marker=$CONFIRM_MARKER"
    case "$INJECT_RESULT" in
        accepted|unknown) ;;
        *) STATUS="fail(inject-$INJECT_RESULT)"; gclear_fallback "inject-$INJECT_RESULT"; return 1 ;;
    esac

    # 5) marker confirmation
    if ! cc_marker_wait "$CC_NEW_SID" "$GCLEAR_INJECT_CONFIRM"; then
        [ "$STATUS" = 'fail(misdelivery)' ] && return 1
        SUBMIT_RESULT="unconfirmed(0retry)"
        STATUS='fail(submit-unconfirmed)'
        gclear_fallback submit-unconfirmed
        return 1
    fi
    VERIFIED_BY=cc-api
    SUBMIT_RESULT='ok(0retry)'; STATUS=success
    return 0
}

cc_resume_inject() {
    CC_NEW_SID="$OLD_SID"
    SUBMIT_TS=$(gclear_stamp)
    cc_checked_send "$INJECT" "$OLD_SID" inject "$GCLEAR_INJECT_RETRIES" || true
    INJECT_RESULT="$CC_SEND_RESULT"
    handoff_receipt inject "$OLD_SID" 2>/dev/null || true
    case "$INJECT_RESULT" in
        accepted|unknown) ;;
        *) STATUS="fail(inject-$INJECT_RESULT)"; gclear_fallback "inject-$INJECT_RESULT"; return 1 ;;
    esac
    if ! cc_marker_wait "$OLD_SID" "$GCLEAR_INJECT_CONFIRM"; then
        [ "$STATUS" = 'fail(misdelivery)' ] && return 1
        STATUS='fail(submit-unconfirmed)'; gclear_fallback submit-unconfirmed; return 1
    fi
    VERIFIED_BY=cc-api; SUBMIT_RESULT='ok(0retry)'; STATUS=success
    return 0
}
