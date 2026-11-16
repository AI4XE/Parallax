---
name: gclear
description: "Generic cross-session context hand-off — persist the context to keep, clear the current session (/clear), and let a clean new session continue with a single prompt. Idle gate + phased idempotent retries + strict delivery receipt + failure fallback guidance; dual mode for CC and CLI. Trigger words: gclear, clear and hand off, cross-session context hand-off."
user_invocable: true
icon: 🧼
portable: true
version: 2.0.1
display_lang: en
---

# gclear 🧼 — Generic cross-session context hand-off (2.0.0)

**Purpose**: after a "discussion/planning" phase has accumulated a lot of context, persist the refined context needed for execution → clear the current session (`/clear`) → let a clean new session **automatically** continue executing with the prompt. Typical chain: requirement alignment → bcheck → **gclear** → clean agent executes → rcheck → commit.

**Copy and use**: copying the whole skill directory (including the vendored dependencies in `lib/` and `gclear.conf`) into any Claude Code project is enough to work. Paths are derived from the script's own location; credentials are read at runtime from `TELEGRAM_STATE_DIR` / `$ROOT/.env`; no configuration change is required. `gclear.conf` is an optional tuning file; the shipped default stays generic and consistent, and local adjustments are allowed, with environment variables overriding item by item.

**Platform prerequisites**: Linux / WSL / macOS behave the same — **Python 3.11+** (tomllib) and the system `ps` are required; the macOS tmux CLI mode additionally needs `tmux` installed (`brew install tmux`). Process information uniformly goes through `ps` (`lib/proc_compat.py`); `cwd` uses `/proc` on Linux and the bundled `lsof` on macOS; when `environ` is unreadable on macOS, infer from "this process's `CLAUDE_CONFIG_DIR` → `~/.claude`", and fail closed if still uncertain. Timeouts and path normalization (replacements for `timeout`/`realpath`) are also implemented inside `handoff.py` and do not depend on GNU coreutils.

**Telegram optional discipline**: If Telegram is not configured, skip notifications silently — never raise errors, never ask the user for tokens or credentials, and never let it affect the main flow (hand-off, injection, and receipts all proceed normally).

**Display language**: display language priority: trigger-message language > explicit user instruction (e.g. "use English" takes effect immediately) > the frontmatter `display_lang` field default; for a bare command with no language signal, read this file's frontmatter `display_lang` value before replying and use it as the default display language.

**Source of correctness**: not "check before delivery", but **verify after delivery + phased idempotent retries**:
- The idle gate uses the transcript's `message.stop_reason` as the primary criterion (`end_turn` = idle; `tool_use`/other = busy; no stop_reason = conservatively busy); `isApiErrorMessage`/system errors are prioritized as abort; a `queue-operation` enqueue needs a matching dequeue **or remove**, and the queue count is settled per **turn window** — `end_turn` clears all previous enqueues, and only enqueues that arrive **after the last turn ended** count as still queued (otherwise cc-connect's legitimate behavior of writing only enqueue and not dequeue when interjecting during a busy period would make the session permanently busy); ignore `isMeta`/`isSidechain`/`isCompactSummary` and `<command-name>`/`<task-notification>`/`<local-cmd-caveat>`. daemon.log is only log corroboration and must never alone determine idleness. On busy-timeout the ledger additionally prints `idle-detail` (queue remnants, last end_turn time) to distinguish "truly busy" from "ledger residue".
- Hard conditions for clear confirmation: **exactly one new session** (SID≠old, cwd=project root, time ≥ submission time) **and** the new transcript contains a `/clear` command entry. Once a new session appears, clearing again is forbidden (prevent the 12:25 double-clear).
- Inject receipt contract: `rc=0` do not resend; `rc=1` transport unknown, do not auto-retry; `rc=2` **only `session busy` may be retried**, other async failures are not retried; `rc=3` fatal. The marker must land in the target SID; landing in the old session → `fail(misdelivery)`.
- Failure fallback: write `.operations/fallback-<token>.json` (0600) + Telegram guidance (with old/new SID, full prompt, recovery command, prohibition on repeated /clear), and provide `--resume-inject` to inject only without clearing.

**Identity anchoring (no session crossing)**: prefer `CLAUDE_CODE_SESSION_ID` → `~/.claude/sessions/<pid>.json` to lock **the caller's own** claude process, then check whether that process's ancestor chain contains this project's cc daemon (CC) or whether it is inside the `$TMUX_PANE` process tree (CLI). If a unique match is impossible → fail closed; never guess with "exactly one process in the same project / the latest transcript / live". Multiple clear candidates → `fail(clear-ambiguous)`.

**Budget**: normal internal budget 1500s; supervised (external supervisor passes `GCLEAR_OPERATION`+`GCLEAR_DEADLINE`≤900) keeps the external deadline value, with an internal phase cap of 840s, and never rewrites `GCLEAR_DEADLINE`.

## Support matrix (2.0.0)

| Invocation environment | Supported | Criterion |
|---|---|---|
| CC (cc-connect daemon pipe) | ✅ | caller anchor hit (process ancestor chain contains this project's daemon) |
| tmux CLI (claude inside a tmux pane) | ✅ | caller anchor hit, or `$TMUX_PANE` non-empty and exact pane determination |
| Bare CLI (no daemon pipe, not tmux) | ❌ mechanically rejected | anchor miss + `$TMUX_PANE` empty → `fail(no-driver)` exit 2 (no injection channel, hand-off is impossible by design; rejection prevents legacy scoped detection from mistaking a live daemon in the same project for a cc session and clearing the wrong session). Guidance: use CC or tmux CLI mode instead |
| qgo executor context | ❌ mechanically rejected | `QGO_EXECUTOR_ID` non-empty → `fail(executor-context)` exit 2 (executor session lifecycle belongs to the orchestrator/driver; self-clear is forbidden) |

All rejection paths write the ledger and send a Telegram notification (with no credentials, notifications are skipped silently and do not block).

## Interface

`gclear.sh "<prompt>" [--context <path>] [--no_file] [--model <name>] [--resume-inject]`
- Positional argument: the sentence given to the new session; omitted → built-in default (automatically references the context file).
- `--context`: context file; defaults to `gclear_context.md` in this directory.
- `--no_file`: clear + inject only, without attaching a context file.
- `--model`: switch model after a successful clear and before injection; any non-empty string without whitespace is accepted (alias mapping is in `gclear.conf`'s `MODEL_ALIASES`; see `--help` for full details).
- `--resume-inject`: **do not clear**; only re-inject the prompt into the current session (used when clear took effect but injection was not confirmed).
- `-h/--help`: interface + model allowlist.

## Execution steps (after the main agent invokes it)

1. (Unless `--no_file`) write the context file with Write (defaults to `gclear_context.md` in this directory). gclear only reads its path and never parses the content.
2. Launch detached from the old call tree with `lib/detached_exec.py` (a missing vendored copy is an error; no cross-skill fallback), with the prompt as a separate argv, not shell-joined. **It must be launched in the background** (`nohup … &`/the tool's background execution) — the launcher waits for the child to finish before exiting, and a foreground call would be taken over by the tool timeout and would in turn make the session busy:

   ```bash
   SKILL_DIR="<absolute path of this skill directory>"
   PYTHON="<interpreter that passed the 3.11/tomllib capability check>"
   nohup "$PYTHON" "$SKILL_DIR/lib/detached_exec.py" \
     --log "$SKILL_DIR/gclear-core.log" --state-dir "$SKILL_DIR/.operations/launcher" \
     -- bash "$SKILL_DIR/gclear.sh" "<prompt>" [--context <path>] [--no_file] [--model <name>] \
     >/dev/null 2>&1 &
   disown 2>/dev/null || true
   ```

   After launching, track progress with `.operations/launcher/<operation>.json` and `gclear-core.log`; do not wait in the foreground.

3. Send a brief receipt and end this turn (Telegram sources use the reply tool). The final state is notified separately by the script via Telegram; on failure, follow the fallback file's guidance.
4. busy may wait up to `GCLEAR_BUSY_TIMEOUT` (default 300s), and injection confirmation up to `GCLEAR_INJECT_CONFIRM` (default 180s), both constrained by the total budget.

> Ledger: every invocation appends a structured record to `gclear.log` (status/mode/SID/receipt/submission/notification). Runtime artifacts (`.tmp/`, `.operations/`, logs, context files) are all gitignored.
