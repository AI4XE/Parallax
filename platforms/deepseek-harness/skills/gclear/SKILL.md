---
name: gclear
description: Same-window context hand-off — after refining the necessary state, request a model-context replacement through the native parallax_handoff, preserving the same SID, agent, user conversation and subagents, and continuing automatically at a safe point; verify durability through the operation status and never clear twice. Trigger words: gclear, clear and hand off, cross-session context hand-off.
whenToUse: When a discussion has accumulated a lot of context and needs a same-window refined hand-off (solo direct execution, team orchestrator continuation), or the user explicitly mentions gclear / hand-off / cross-session transfer.
user-invocable: true
metadata:
  portable: true
  version: 2.0.0
  display_lang: en
---

# gclear — Same-window context hand-off (DSH edition, 2.0.0)

**Purpose**: persist the refined state needed to continue, or write it into a small payload → request a native safe-point replacement of the model surface → continue automatically in the **same SID, same agent, same window**. Typical chain: requirement alignment → independent bcheck → **gclear** → current session executes → independent rcheck → commit → xwrap.

The native plugin separates the model input surface from the user-visible transcript: the old chat history remains, and the window and composer stay unchanged; necessary system/safety rules, new user input, subagent notifications and existing parent-child relationships are preserved. It does not start another process or new SID, and does not stuff the full history back into the hand-off payload.

**Capability boundary**: the **complete native bundle** that provides the `parallax_handoff` tool and the safe-point hook must be installed. Copying the skill text alone does not provide this capability; if the tool is unavailable or the version unsupported, explicitly report "this environment does not support native gclear; install a supported complete bundle", stop the hand-off, and never pass off a normal compact, a background process or in-place continuation as success. The standard native plugin installation configures `maxDepth=3` with the bundle; the user does not need to edit configuration. Do not bypass an unsupported environment via bypass, extra add-dir, permission changes or configuration.

**Role boundary**: the tool is visible and executable only to the top-level agent; the top-level solo executor and the team orchestrator may use it. The team executor and its review subagents do not use this management tool; the executor's hand-over is arranged by the parent orchestrator through an independent native subagent after a safe checkpoint; do not feed this protocol or its operational details to the executor.

**Display and channel**: display language priority is trigger-message language > explicit user instruction > frontmatter `metadata.display_lang`. With an external messaging channel, reply along that channel; otherwise reply in the normal session, do not ask for credentials, and do not duplicate across channels.

## Tool contract

Use a normal tool call to `parallax_handoff`, not a shell command:

- `operation_id`: **required**, the unique identifier of one hand-off; record it in the current task state before the request, and use the same id for later queries or cancellation — do not guess "the latest operation".
- `action`: `request` (default), `status` or `cancel`.
- `summary`, `continue_with`: **both required** for `request`; together no more than **12000 characters**. The former is the necessary state summary; the latter is the instruction executable in the first turn after the safe point (including verifying this operation's status, reading the carrier/role template, and the next step). Cite exact anchors; do not inline the whole history or plan.
- `carrier`: optional, and must be a file that **already exists and is located in the calling agent's workspace**. Resolve the path against that workspace, and first use normal file tools to confirm it exists, is complete and belongs to this task. When a cross-repo task root is outside the calling workspace, do not use that out-of-workspace path as the carrier; instead save the necessary state in a permitted temp directory of the calling workspace and cite exact task anchors. The plugin does not replace file tools for reading arbitrary private files.
- `status` / `cancel` need only the corresponding `operation_id` and `action`; do not query whether it took effect by sending another `request`.

File-free hand-off applies only to tasks whose small payload is self-sufficient; do not force the user's project to add configuration files.

## Execution steps

1. **Verify identity and stage**: confirm you are the top-level agent for this task, with the exact host/Git root, task root, workspace and role; qgo must first complete an independent bcheck with PASS≥4. The team orchestrator records the in-flight executor id/state, orchestration records and completion/handoff anchors, and must not dispatch duplicate work because of its own hand-off.
2. **Prepare the necessary state**: when a file is needed, use normal tools to write the summary into a permitted temp directory of the calling workspace (default `<calling workspace>/tmp/gclear/gclear_context.md`); if a same-named file exists, verify ownership first and do not overwrite another task. Organize the content per the caller's role template: solo references qgo's `templates/solo.md`, and the team orchestrator references `templates/orchestrator.md`, both with exact paths. Keep goals, approved plan/discussion anchors, the user's latest requirements, the current stage, completed evidence, open items, next steps, commit scope and protection of existing dirty state; for team, also record existing subagents and heartbeat ownership.
3. **Send one request**: first record the `operation_id`, call `action=request`, and provide the refined `summary`, `continue_with` and optional `carrier`. `continue_with` must state explicitly that the first turn should query this id first, then read the role template/carrier and continue with the next step. After the request, do not start other hand-off or continuation processes, and do not treat completion of the business task as the criterion for request delivery.
4. **Understand the receipt correctly**: a normally accepted request returns `state=requested,durable=false,intent_durable=true`: **the pre-intent is durable, but the current tool result has not yet been appended, so you must not claim that the receipt or hand-off is durable**. This is not a failure: do not retry the request and do not wait for `durable=true` during the requested stage. End the current tool turn normally; at the next `agent/pre-step` safe point the native tool result is flushed first, then the surface is replaced in the same SID/agent; the next model turn resumes automatically without waiting for the user to say "continue". The safe point preserves new user input and subagent messages that arrive after the request.
5. **First-turn verification after hand-off**: call `action=status` for the same id and judge per the table below. Verify the status first, then read the carrier and role template; for team, verify the existing executor first, then resume supervision. Verify the hand-off effect with the currently valid model message/native meter or real request evidence; old log usage is only corroboration — a short history may have nothing reducible, so do not invent a drop percentage.
6. **Continue the original task and receipt**: after confirming the status, proceed automatically without dropping requirements added during the hand-off; briefly state the actual hand-off status and next step along the current channel. A successful hand-off does not mean business implementation or independent review is complete.

## Status, failure and recovery

| Status | Meaning and handling |
|---|---|
| `state=requested,durable=false,intent_durable=true` | **Normally accepted request**: only the pre-intent is durable; the current tool result has not been appended and the surface has not been replaced. Wait for the safe point to flush the result and apply. Not a failure; do not require requested+durable=true; query the original id, do not resend the request, and do not claim the context has been replaced. |
| `applied`, `durable=true` | The surface has been replaced and durability is confirmed; continue the original task in the same session. |
| `applied`, `durable=false` | **The surface has changed but durability is unconfirmed** (including flush=false/reject); keep the carrier, operation and error evidence, report truthfully, query the original id for acknowledgement, **do not clear again or redo with a new id**, and do not claim full success. |
| `failed` | Failed before replacement; the old surface is preserved; report the specific error and the carrier/operation, investigate before deciding what to do next, and do not retry blindly. |
| `cancelled` | Cancelled and must not be applied; after verifying the status, continue on the preserved old surface and do not report the context as replaced. |

- If the request has not been applied and genuinely needs to be withdrawn, call `action=cancel` and verify the returned status; if already applied, do not falsely claim the cancellation undid it, and do not roll back history.
- On request error, timeout, missing receipt, intent-persistence failure, unconfirmed applied durability or restart recovery, **query by the original operation_id first**; do not probe with a new request. A normal requested `durable=false` is not one of these failure branches. If the status cannot be confirmed, explicitly report it as unconfirmed and keep the carrier; do not infer success or automatically clear again.
- The same id with the same content is applied only once; the same id with a different payload is rejected, and an id cannot be reused for another task. Querying an applied operation only reads existing evidence and does not replace post-hand-off work.
- `intent_durable=true` does not mean the current tool result or the replacement is durable; only `applied,durable=true` confirms a complete hand-off. Neither a resolved promise nor an existing file proves success. The plugin recovers request state from a successful native `tool/result`, replays through native append-only session events, and does not depend on custom events, private APIs or injecting user messages mid-tool-execution; it does not directly rewrite, delete or roll back session records.
- After a process restart, restore the **original session** through the harness's normal entry point, query by the recorded operation_id, then continue per the carrier; do not create a new SID as gclear recovery, and do not open the same session concurrently.

## Artifact discipline

The hand-off carrier and the brief operation status record go in this task's temp directory inside the calling workspace; do not write generated plugin packages, leave credentials or model request bodies, or check them into version control by default. Recording identity, role, operation_id, carrier anchors and actual status is enough; existing session transcripts and executor artifacts are always preserved. Skill text is reusable across projects, but the native capability always depends on the complete bundle; a skills-only install must not be presented as equivalent.
