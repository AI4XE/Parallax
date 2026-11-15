---
name: qgo
description: Q workflow hand-off execution: re-check the discussion → plan → independent review (bcheck, PASS≥4) → gclear automatic hand-off to a clean session → the hand-off session executes autonomously → rcheck reconciliation → scoped commit → final receipt (solo direct-execution tier). Trigger words: qgo, Q hand-off development.
version: 2.0.0
---

# qgo — Hand-off execution

The user explicitly saying qgo, or an explicit hand-off in the Q discussion, authorizes progress without repeated confirmation. Read the exact discussion anchor for this run and the current requirement; do not guess ownership from "the most recent file". infra (non-version) and hotfix (version) share the backbone below; respect an explicit user instruction to use another flow. This skill ships its own protocol and templates; do not read other hand-off skills or host-private scripts to fill in the logic.

Display language: follow the language of the user's current message (default to English when there is no textual signal). Chat replies are the receipt — the chat message itself is the delivery channel; there is no other channel, so do not send duplicates.

Runtime prerequisites: this suite is installed in the current workspace (the seven skills qstart/qgo/bcheck/gclear/rcheck/xwrap/wish (including wish) under `.github/skills/`, plus the session-management tools). Cross-session hand-off is handled by the gclear skill; this skill does not duplicate its mechanism.

**Core structure of solo direct execution (the only tier)**: this session handles "re-check the discussion → write the plan → independent review"; after passing, gclear automatically hands off to a clean new session; the hand-off session is itself the executor and works straight through to the final receipt. No third session participates, and there is no background process to supervise or reclaim.

## 1. From discussion to reviewed plan

1. Re-check the discussion's complete overview, the user's latest requirements, and unresolved contradictions. Proceed autonomously when there are none; for contradictions that still need user judgment, ask one question at a time with options, a recommendation and a brief reason. Do not re-interrogate settled directions.
2. Lock mode, workspace/Git root, task root, actual version source and sync points, target version, document and commit ownership. Use the overview and the user's latest explicit instruction; never silently change the type by re-inferring. infra does not bump versions artificially; hotfix updates in sync with the project's actual version source.
3. Write the detailed analysis, execution plan and checklist, citing the exact discussion. Create the directory if it does not exist. By default infra uses host `docs/dev/infra/infra-plan-{discussion-timestamp}.md` and hotfix uses `docs/dev/hotfix/v{target-version}-analysis.md`; existing project conventions take precedence. If a same-named document exists, verify ownership first, do not overwrite other tasks, and use a project/task suffix compatible with the original glob if needed. Must include: goals/scope/completion criteria, rationale and plan, real verification, affected modules, pre-test cleanup, risks, **independent rcheck before commit**, hand-off-side xwrap, **reference context** (for a clean session). Put necessary differences in task material; do not copy the whole plan into a template.
4. Invoke the **bcheck skill**, following that skill's independent model, rounds and scoring rules; continue only on **PASS≥4**. Respond to each concern and re-review after fixes. Do not mistake plan review for implementation verification.
5. Weave personalized instructions appended at the call tail or during the process into the plan and hand-off; they stack on the core and do not replace gates. Neither task type introduces pre-splitting or additional gates.

## 2. Automatic hand-off (gclear)

After bcheck passes, invoke the **gclear skill** to complete the cross-session hand-off:

- Hand-off carrier = `docs/dev/handoff/gclear-context.md` (the gclear skill defines the exact format), containing: the exact path of the approved plan, the solo executor role description (role authority = this skill's `templates/solo.md` exact path; the hand-off session reads it first), the refined task context (reviewed plan, anchors, user decisions, current stage, mode/version source, allowed commit scope), and this hand-off's run token.
- Hand-off prompt = one sentence: read the hand-off context file → act as the executor → the first reply must carry `HANDOFF-ACK:<token>` → execute per the plan.
- Verification after delivery, idempotent retries and the escape hatch all follow the gclear skill; this skill does not reimplement them.
- After the hand-off is confirmed, this session gives the user a brief receipt (the new session has taken over, expected artifact anchors), then ends its turn normally. **This session does not continue executing the task afterwards** — execution authority has been fully transferred to the hand-off session.

## 3. Hand-off executor discipline (solo)

The hand-off session acts as the executor per `templates/solo.md`, flow: implementation → real-environment verification → invoke the **rcheck skill** for independent reconciliation (**PASS≥4**; fix and re-review issues) → scoped local commit → invoke the **xwrap skill** to finish todo/wrapup and necessary commits → the final receipt **directly in chat** (task identity, latest requirements, rcheck/wrapup anchors, all actual commits, test conclusions, deviations/not done/outstanding verification).

- The executor only writes task artifacts and hand-off material; **do not archive/delete any chat session** (this flow has no such need; that capability is never used).
- When the user explicitly waives a step (e.g. xwrap), follow the task material and state it truthfully.
- When the task is expected to be heavy or clearly grows mid-way, tell the user honestly and stop at a safe point to ask for help rather than forcing it through.

## 4. Delivery boundary

The final receipt is bounded by the hand-off executor completing xwrap (or an explicit user waiver) and necessary commits; it is not replaced by a business commit or a sentence like "done". The original session (the hand-off initiator) exits the task role once the hand-off is confirmed; later user adjustments go directly to the current execution session, and another gclear round can be run when needed.
