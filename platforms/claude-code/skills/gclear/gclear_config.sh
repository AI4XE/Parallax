#!/bin/bash
# gclear 1.2.4 configuration layer.
#
# Loads the single shared gclear.conf, restores environment overrides (env wins),
# maps the three legacy key names, detects supervised mode from the existing
# GCLEAR_OPERATION / GCLEAR_DEADLINE entry points, and derives paths/credentials
# at runtime. Nothing project-specific is read from the config file.

GCLEAR_CONF_KEYS="GCLEAR_BUSY_TIMEOUT GCLEAR_IDLE_STABLE GCLEAR_POLL_INTERVAL
GCLEAR_INTERRUPT_ENABLE GCLEAR_INTERRUPT_GRACE GCLEAR_INTERRUPT_ATTEMPTS
GCLEAR_NOTIFY_FALLBACK GCLEAR_NOTIFY_DRYRUN
GCLEAR_CLEAR_CONFIRM GCLEAR_CLEAR_LATE GCLEAR_CLEAR_RETRIES
GCLEAR_MINIMAL_NOTIFY GCLEAR_CLEAR_MESSAGE GCLEAR_CLEAR_FAILURE
GCLEAR_INJECT_CONFIRM GCLEAR_INJECT_RETRIES
GCLEAR_MODEL_CONFIRM GCLEAR_MODEL_RETRIES GCLEAR_TOTAL_BUDGET
GCLEAR_SUPERVISED_PHASE_CAP GCLEAR_SUPERVISED_INTERRUPT_ATTEMPTS
GCLEAR_SUPERVISED_CLEAR_LATE GCLEAR_SUPERVISED_INJECT_CONFIRM
GCLEAR_SUPERVISED_MODEL_CONFIRM"

gclear_config_load() {
    local key saved="" val
    # Snapshot environment overrides before the conf file assigns defaults.
    for key in $GCLEAR_CONF_KEYS; do
        if declare -p "$key" >/dev/null 2>&1; then
            val="${!key}"
            saved="${saved}${key}=${val}\n"
        fi
    done
    if [ -r "$SELF_DIR/gclear.conf" ]; then
        # shellcheck disable=SC1091
        . "$SELF_DIR/gclear.conf"
    fi
    # Restore any value that came from the environment.
    while IFS='=' read -r key val; do
        [ -n "$key" ] || continue
        printf -v "$key" '%s' "$val"
    done < <(printf '%b' "$saved")

    # Legacy key names (correct spellings; never map the system TMPDIR).
    [ -n "${GCLEAR_CC_BUSY_TIMEOUT:-}" ] && GCLEAR_BUSY_TIMEOUT="$GCLEAR_CC_BUSY_TIMEOUT"
    [ -n "${GCLEAR_CC_BUSY_POLL:-}" ] && GCLEAR_POLL_INTERVAL="$GCLEAR_CC_BUSY_POLL"

    # Work scratch lives INSIDE the skill directory so copying the skill carries
    # it along and projects never collide.
    WSTMP="${GCLEAR_TMPDIR:-$SELF_DIR/.tmp}"
    mkdir -p "$WSTMP" 2>/dev/null
    export WSTMP

    # Supervised mode comes from the existing watcher entry points only.
    GCLEAR_SUPERVISED=0
    if [ -n "${GCLEAR_EXTERNAL_OPERATION:-}" ] || [ -n "${GCLEAR_EXTERNAL_DEADLINE:-}" ]; then
        GCLEAR_SUPERVISED=1
    fi
    if [ "$GCLEAR_SUPERVISED" = 1 ]; then
        [ "$GCLEAR_INTERRUPT_ATTEMPTS" -gt "$GCLEAR_SUPERVISED_INTERRUPT_ATTEMPTS" ] && \
            GCLEAR_INTERRUPT_ATTEMPTS="$GCLEAR_SUPERVISED_INTERRUPT_ATTEMPTS"
        [ "$GCLEAR_CLEAR_LATE" -gt "$GCLEAR_SUPERVISED_CLEAR_LATE" ] && \
            GCLEAR_CLEAR_LATE="$GCLEAR_SUPERVISED_CLEAR_LATE"
        [ "$GCLEAR_INJECT_CONFIRM" -gt "$GCLEAR_SUPERVISED_INJECT_CONFIRM" ] && \
            GCLEAR_INJECT_CONFIRM="$GCLEAR_SUPERVISED_INJECT_CONFIRM"
        [ "$GCLEAR_MODEL_CONFIRM" -gt "$GCLEAR_SUPERVISED_MODEL_CONFIRM" ] && \
            GCLEAR_MODEL_CONFIRM="$GCLEAR_SUPERVISED_MODEL_CONFIRM"
    fi
    export GCLEAR_SUPERVISED GCLEAR_BUSY_TIMEOUT GCLEAR_IDLE_STABLE GCLEAR_POLL_INTERVAL
    export GCLEAR_INTERRUPT_ENABLE GCLEAR_INTERRUPT_GRACE GCLEAR_INTERRUPT_ATTEMPTS
    export GCLEAR_CLEAR_CONFIRM GCLEAR_CLEAR_LATE GCLEAR_CLEAR_RETRIES
    export GCLEAR_MINIMAL_NOTIFY GCLEAR_CLEAR_MESSAGE GCLEAR_CLEAR_FAILURE
    export GCLEAR_INJECT_CONFIRM GCLEAR_INJECT_RETRIES GCLEAR_MODEL_CONFIRM GCLEAR_MODEL_RETRIES
}

# Telegram credentials. BOT_TOKEN: TELEGRAM_STATE_DIR/.env first, then
# $ROOT/.env. CHAT_ID: only $ROOT/.env (removes every cwd dependency).
gclear_credentials() {
    BOT_TOKEN=""
    CHAT_ID=""
    local state_env
    if [ -n "${TELEGRAM_STATE_DIR:-}" ] && [ -r "$TELEGRAM_STATE_DIR/.env" ]; then
        state_env="$TELEGRAM_STATE_DIR/.env"
        BOT_TOKEN=$(sed -nE 's/^TELEGRAM_BOT_TOKEN=["'"'"']?([^"'"'"']*)["'"'"']?[[:space:]]*$/\1/p' "$state_env" | head -1)
    fi
    if [ -r "$ROOT/.env" ]; then
        [ -n "$BOT_TOKEN" ] || BOT_TOKEN=$(sed -nE 's/^TELEGRAM_BOT_TOKEN=["'"'"']?([^"'"'"']*)["'"'"']?[[:space:]]*$/\1/p' "$ROOT/.env" | head -1)
        CHAT_ID=$(sed -nE 's/^TELEGRAM_OWNER_CHAT_ID=["'"'"']?([^"'"'"']*)["'"'"']?[[:space:]]*$/\1/p' "$ROOT/.env" | head -1)
    fi
    # Never send to a malformed owner id; treat it as absent (portable, no hard fail).
    case "$CHAT_ID" in ''|0*|*[!0-9]*) CHAT_ID="" ;; esac
    export BOT_TOKEN CHAT_ID
}
