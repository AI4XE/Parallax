---
name: gclear
description: Generic cross-session context hand-off — persist the context to keep, automatically create a clean new session and inject the hand-off prompt to continue; verify by reading back after delivery + idempotent retries + failure escape hatch. Trigger words: gclear, clear and hand off, cross-session context hand-off, hand off to a new session.
version: 2.0.0
---

# gclear — Generic cross-session context hand-off

**Purpose**: after a "discussion/planning" phase has accumulated a lot of context, persist the refined context needed for execution → **automatically create a clean new session and deliver the hand-off prompt** → let the clean session continue executing. Typical chain: requirement alignment → bcheck → **gclear** → clean session executes → rcheck → commit.

**Essential difference from platforms with a `/clear` primitive (understand truthfully; do not apply the old mechanism)**: this platform has no programmable "clear the current session" interface, and does not need one — hand-off semantics = "persist the context → `create_session` to start a clean session → `send_message` to deliver the hand-off prompt → read back to verify". The current session **does not clear, reset, archive or delete itself**: it persists the hand-off material, creates the hand-off session, reports the result and then ends its turn naturally. The new session's context is naturally clean (a fresh session with zero history), so the risk of "old context polluting execution" structurally does not exist on this platform.

**Display language**: follow the language of the user's current message (default to English when there is no textual signal).

## Interface

The main session follows this contract (usually invoked by qgo after bcheck passes; a manual user invocation is equally valid):

- **Hand-off prompt** (required): the sentence given to the new session; omitted → built-in default (tells the new session to read the context file first and act according to its contents).
- **Context file** (optional parameter `--context`): defaults to `<task root>/docs/dev/handoff/gclear-context.md`. gclear only persists it and references its path, and never rewrites the semantics of content given by the caller.
- **`--no_file`**: create the new session + deliver only, without writing a context file (the prompt must contain all required information itself).

## Source of correctness: verify after delivery + run-token idempotency

The core principle is **verify after delivery, not check before delivery**. A read-back failure ≠ not delivered (older machines have slow model steps and context updates are delayed); **every retry decision is based on the ack read back**:

- **run token**: a unique marker generated at persist time, format `gc-<YYYYMMDD-HHMMSS>-<4-char random string>` (e.g. `gc-20261113T101530-a3f2`). It is written into the context file and the hand-off prompt and runs through the entire hand-off.
- **Hand-off receipt contract**: the new session's first reply must contain `HANDOFF-ACK:<token>` (on its own line). This is the sole criterion for successful delivery.
- **Always check the ack before resending**: when a verification failure needs a retry, you **must first `get_session_context` to read back the target session** and check whether `HANDOFF-ACK:<token>` already exists — already acked → only wait, do not resend; not acked → retry.
- **Idempotency**: the same context file + the same token run through all retries; if the hand-off session sees a duplicate delivery with a repeated token, it ignores the duplicate and continues according to the already-acked content.
- **Retry limit**: unacked retries ≤2 (initial delivery + at most 2 resends).
- **Failure fallback (escape hatch)**: when automation is exhausted, output a **ready-made prompt** + guidance to the user (open a new chat manually and paste it to continue; includes the context file path and token), and explicitly declare that this automatic hand-off failed and is to be recorded as a defect. The escape hatch is an emergency channel, not the normal path.

## Execution steps (the main agent runs these in order after invocation)

1. **Persist** (unless `--no_file`): write the hand-off context to `<task root>/docs/dev/handoff/gclear-context.md` (create the directory first if missing; if the directory already has context files for other tasks, add a task suffix to distinguish them and do not overwrite). The file must contain four elements:
   - **run token** (newly generated for this hand-off, placed prominently near the first line of the file)
   - **Plan file path** (the exact path of the reviewed plan/analysis document)
   - **Role description**: the hand-off session is the solo executor — role authority = the qgo skill's `templates/solo.md` (write the exact path and prompt to read it first); if that template is missing, fall back to the refined role description embedded in the context file (hand-off execution → real verification → independent rcheck (PASS≥4) → scoped local commit → xwrap → final receipt sent directly in chat)
   - **Refined task context**: reviewed plan essentials/anchors, user decisions, current stage, mode/version source, allowed commit scope. Do not copy the full planning history.
2. **Create the hand-off session**: call `create_session`; **workspace binding is mandatory** (value = the target task root / project root path; creation without a workspace is rejected). Do not pass a worktree parameter (solo hand-off happens in the same workspace). Record the returned new session identifier.
3. **Deliver the hand-off prompt**: call `send_message` with a one-sentence hand-off prompt, template:

   > Read `<exact context file path>` (run token for this hand-off = `<token>`): act as the solo executor per the file's contents and take over. Your first reply must begin with `HANDOFF-ACK:<token>` on its own line, then continue executing according to the plan in the context file.

   If the caller supplies a custom prompt, it must still include: the context file path, the run token, and the `HANDOFF-ACK:<token>` first-reply requirement.
4. **Verify after delivery**: use `get_session_context` to read back the new session and confirm the prompt was received and `HANDOFF-ACK:<token>` appeared or there are real signs that plan execution has started. Allow the new session reaction time when reading back (a model step may take tens of seconds); if the first read shows nothing, wait and read again — do not immediately judge failure.
5. **Retry on failure** (when verification does not pass): first read back and check the ack (see above) → retry only if unacked: same token, same context file, `send_message` again, ≤2 times. Read back to verify after each retry.
6. **Receipt**: after verification passes, give the user a brief receipt (the new session has taken over + token + expected artifact anchors), then end the turn normally; the final status is delivered directly by the hand-off session. If verification is exhausted and fails → use the "escape hatch" to output the ready-made prompt and guidance, and truthfully declare the failure.
7. **Permission-tier differences (understand truthfully; do not change the flow)**: when the session's default tier is Manual, the first calls to `create_session`/`send_message` each raise one approval prompt — after approving once, same-kind calls in the same session do not prompt again; if the user has enabled a zero-confirmation tier (Allow all/Bypass Approvals/Autopilot, setting key `chat.defaultConfiguration`), there are no confirmations at all. The flow steps are identical under both tiers; only the number of approvals differs. **Do not interrupt or reroute because of an approval prompt.**

## Hand-off material conventions

- The hand-off carrier directory `docs/dev/handoff/` is a runtime artifact area: the context file is for traceability and is not tracked by default (whether to commit is decided by the project's commit discipline; it is not mandatory).
- One hand-off, one token; multiple hand-offs across tasks each have independent tokens and are never reused.
- The hand-off session's responsibility boundary (write the receipt, no self-clear, do not manage session lifecycle) is defined in the qgo skill's `templates/solo.md`.
