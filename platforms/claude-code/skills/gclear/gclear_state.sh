#!/bin/bash
# gclear 1.2.4 state helpers: idle/queue/identity evidence + test hooks.
# All transcript semantics live in handoff.py; this file is the bash wrapper so
# both the CC and CLI adapters share one source of truth.

gclear_idle() {                       # <sid> -> idle|busy
    "$CC_PYTHON" "$SELF_DIR/handoff.py" idle "$ROOT" "$1" 2>/dev/null
}

gclear_idle_detail() {                # <sid> -> JSON diagnostic for busy-timeouts
    "$CC_PYTHON" "$SELF_DIR/handoff.py" idle-detail "$ROOT" "$1" 2>/dev/null
}

gclear_discover() {                   # <old_sid> <since> -> ok <sid>|ambiguous <..>|none
    "$CC_PYTHON" "$SELF_DIR/handoff.py" discover "$ROOT" "$1" "$2" 2>/dev/null
}

gclear_has_clear() {                  # <sid> <since> -> 1|0
    if "$CC_PYTHON" "$SELF_DIR/handoff.py" has-clear "$ROOT" "$1" "$2" 2>/dev/null; then
        echo 1
    else
        echo 0
    fi
}

gclear_caller() {                     # -> cc|cli <sid> <pid>
    "$CC_PYTHON" "$SELF_DIR/handoff.py" caller "$ROOT" 2>/dev/null
}

gclear_pane() {                       # <pane> -> <sid>\t<used>
    "$CC_PYTHON" "$SELF_DIR/handoff.py" pane "$ROOT" "$1" 2>/dev/null
}

gclear_pane_model() {                 # <pane> -> model
    "$CC_PYTHON" "$SELF_DIR/handoff.py" pane-model "$ROOT" "$1" 2>/dev/null
}

gclear_stamp() {                      # epoch seconds with sub-second precision
    "$CC_PYTHON" -c 'import time; print(time.time())'
}

# Test hooks (used by the A-G matrix; inert by default). They sleep through
# handoff_sleep so the shared budget/deadline still applies.
gclear_test_pause() {                 # <ENV_NAME>
    local name="$1" value
    value="${!name:-0}"
    case "$value" in ''|0|*[!0-9]*) return 0 ;; esac
    log "TEST HOOK $name=${value}s"
    handoff_sleep "$value"
}

# Wait for a stable idle window. Returns 0 idle, 1 timeout/cancel.
# Transcript `stop_reason` is the primary signal. daemon.log is only written to
# the log as corroboration and never overrides the transcript.
gclear_wait_idle() {                  # <sid> <timeout> <stable>
    local sid="$1" timeout="$2" stable="$3"
    local waited=0 hits=0 interval="$GCLEAR_POLL_INTERVAL" state
    gclear_test_pause GCLEAR_PAUSE_BEFORE_IDLE || return 1
    while [ "$waited" -lt "$timeout" ]; do
        handoff_guard || return 1
        state=$(gclear_idle "$sid")
        case "$state" in
            idle)
                hits=$((hits + 1))
                if [ "$hits" -eq 1 ]; then
                    _idle_since=$SECONDS
                elif [ $((SECONDS - _idle_since)) -ge "$stable" ]; then
                    log "idle confirmed (stable ${stable}s, transcript)"
                    return 0
                fi ;;
            busy) hits=0 ;;
            *)    hits=0 ;;
        esac
        [ "$waited" -lt "$timeout" ] || break
        handoff_sleep "$interval" || return 1
        waited=$((waited + interval))
    done
    log "idle not confirmed within ${timeout}s (last=$state) detail=$(gclear_idle_detail "$sid")"
    return 1
}
