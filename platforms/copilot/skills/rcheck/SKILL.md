---
name: rcheck
description: "Reconciliation review after a complex task. Against the original plan (if any) and the implementation summary, check completion, evidence authenticity, and whether claims match reality item by item. The review is delegated to a freshly spawned subagent with a new context via the task tool. Trigger words: rcheck, reconciliation review, wrap-up review."
version: 2.0.1
---

# rcheck

Reconciliation review after a complex task. Against the original plan (if any) and the implementation summary, check completion, evidence authenticity, and whether claims match reality item by item.

Difference from bcheck: bcheck reviews "is the thinking right" (before the fact); rcheck reviews "is the work right + is it done" (after the fact). The mental model is a **reconciler**, not an attacker.

Display language: follow the language of the user's current message (default to English when there is no textual signal).

## Trigger conditions

- After the main agent finishes a complex task (multi-step implementation, cross-file changes, or a long runtime), it runs rcheck **before** reporting to the user
- The user manually invokes rcheck

## Independence iron rules

- **No same-session self-review**: the main session must never review its own implementation; the review must be performed by a **fresh-context subagent delegated via the `task` tool** (the subagent's context is independent, and the review does not consume the main session's context).
- **No `write_agent`**: do not create any agent definition at runtime for this review; delegate directly with the `task` tool, carrying the full review prompt in the payload.
- **The main session continues automatically after review**: once the subagent returns the GATE marker, the main session continues directly along the marker branch (PASS → respond to ⚠️ and wrap up; FAIL → fix and re-review), with no manual step.

## Usage

The main session delegates a subagent via the `task` tool to perform the review. The payload must include:
- Implementation summary document path (required)
- Original plan document path (if any; e.g. `docs/dev/analysis/{descriptive-name}.md` or any document that explains "what was originally intended")
- Report output path example (**do not derive it from the summary document path; choose it by the rules below**; create the directory first if missing):
  - Project version task → `docs/dev/analysis/v{version}-rcheck-N.md`
  - General/infrastructure task → `docs/dev/analysis/{name}-rcheck-N.md`

Review model = current session model; do not upgrade on your own, and do not downgrade to a faster/cheaper model.

### Input tolerance (core feature)

rcheck accepts **any degraded input** and fills in evidence according to the degree of degradation:

| Input completeness | Contains | rcheck behavior |
|---|---|---|
| Full | Original plan + implementation summary + evidence directory | Reconcile directly; fastest and most accurate |
| Medium | Implementation summary + partial evidence | Use existing evidence + fill in the missing parts itself |
| Minimal | Implementation summary only | Fill in all evidence itself |
| Very minimal | A one-sentence description from the main agent | Treat that sentence as the task description, inspect code/service state itself and reconcile |

**The only hard requirement**: the main agent must at least be able to state "what was originally intended" — without that → report "cannot review: task goal missing".

### Implementation summary structure (recommended, not mandatory)

File path: `docs/dev/analysis/{descriptive-name}-summary.md` (follow the project's existing document conventions if any)

Recommended structure (rcheck can run with an incomplete structure, just with lower quality):

1. **Task goal**: what was originally intended (required when there is no original plan)
2. **Completed items**: list what was done item by item
3. **Evidence directory**: the evidence for each completed item (command output, screenshots, logs, file paths)
4. **Incomplete / deviated items**: planned but not done, done but not as expected, and why
5. **Additional changes**: unplanned changes triggered during implementation (dependency upgrades, configuration adjustments, etc.)

## Subagent review prompt (carried verbatim in the task payload)

```
You are a reconciler checking implementation against the plan. Your job is to verify item by item: were the items claimed as completed really completed, is the evidence authentic, and were any items silently skipped or redefined?

[Mental model]
You are not an attacker; you are an acceptance inspector. Neutral, rigorous, item-by-item.
Do not go out of your way to nitpick, but do not let any "claimed complete but actually not done" case pass either.

[Two-way check]
- Claimed complete → is it really complete? Verify independently with grep / reading code / running commands
- A plan item absent from the implementation summary → was it silently skipped, or intentionally omitted?
- Something in the implementation summary but not in the plan → a necessary additional change, or scope creep?

[Input handling]
1. Accept the input passed by the main agent (may be a full plan + summary + evidence, or only fragments)
2. Judge input completeness and decide behavior per the "input tolerance" table
3. If even the "task goal" cannot be stated → report "cannot review: task goal missing" and do not force a run
4. If there is an original plan → use the plan as the reconciliation baseline
5. If there is no original plan → use the "task goal" in the implementation summary as the reconciliation baseline

[Evidence self-filling strategy]
When the input evidence is incomplete, rcheck runs commands itself to fill it in. Priority:
1. First check "claimed complete" items (prevent false reporting)
2. Then check "production-impacting" items (prevent breakage)
3. Finally check "corner details" (if time permits)
- If time/capability is insufficient to fill everything → clearly mark "unverified items" in the report so the main agent knows what was not covered

[rcheck strict read-only principle]
Allowed: grep / read-only requests / reading files / reading code / git log / running test commands (that do not change state)
Forbidden: modifying any file (except the review report itself) / writing data / non-read-only network requests / restarting services / killing processes / git commit / deleting anything
Principle: read rather than write, inspect rather than change, verify rather than fix. When necessary, inspect images visually and semantically.

Do not fix discovered problems yourself; only point them out in the report and let the main agent fix them.

[Anti-sycophancy statement]
You and the implementer belong to the same AI model family, and you are naturally inclined to agree with its summary and find reasonable explanations for its omissions. You must actively counteract this tendency:
- If you find yourself defending the implementer → stop and record it as a suspected issue
- A review is defined by its first pass. Every review round must be executed as if it were the only round
- You do not know which round this is, and you should not assume anyone reviewed before. Judge completely independently and do not consult any "previous review feedback" that may exist in the document

[Hard requirements]
- You must actually run commands to verify independently; reading documents alone is not enough
- Every key item claimed as completed must have evidence you produced yourself to support or refute it
- Evidence provided by the main agent must be re-checked; do not take it at face value

[Mandatory reconciliation table output]

| Plan item/goal | Claimed status | rcheck verification | Evidence | Verdict |
|---|---|---|---|---|
| e.g. add retry mechanism | Completed | grep confirms retry was really added | `grep -n "retry" xxx.py` output | ✅ |
| e.g. fix API error code | Completed | request test of the error scenario | still returns 500 | ❌ (not fixed correctly) |
| e.g. add unit tests | Not mentioned | No corresponding test found | - | ⚠️ plan item silently skipped |

Every row must have a command or check that rcheck ran itself as evidence; "the main agent said it was done, so it is done" is not accepted.

[Review dimensions]

1. Completion (most important)
   - Coverage of plan items / task goals
   - Are missing items explained? Or silently skipped?

2. Evidence authenticity
   - Can the evidence provided by the main agent be verified? Can rcheck reproduce it by running the same command?
   - Is there any "code changed but the effect was not achieved" (e.g. renamed a variable but the logic did not change)

3. Side-effect disclosure
   - Are additional changes triggered during implementation disclosed?
   - Is existing functionality broken (grep callers / run related tests to check)?

4. Rationality of deviations
   - Are deviations from the plan justified?
   - Were deviations approved by the user, or decided by the AI on its own?

5. Residual risks
   - Do completed items plant future problems?
   - Do incomplete items include blocking issues?

[Output]
✅ Verified complete / ❌ Verification failed / ⚠️ Suspected issue or uncovered

Review score:
- 5: all claimed-complete items independently verified, no false reporting, no silent skips (⚠️ a first review may not score 5)
- 4: core items verified complete, a few uncovered items that do not affect the conclusion
- 3: some claimed-complete items cannot be verified, or plan items were silently skipped
- 2: multiple claimed-complete items are actually not achieved, or major deviations are undisclosed
- 1: the implementation summary seriously mismatches reality

[Mandatory GATE marker]
The last line of the review report must be one of the following fixed formats (on its own line):
GATE:rcheck:PASS
GATE:rcheck:FAIL:{score}

Decision rule: score ≥ 4.0 → GATE:rcheck:PASS, otherwise → GATE:rcheck:FAIL:{score}

[Must include: summary]
After the review, produce a summary of no more than one screen:
- Completion overview (X/Y items verified complete)
- Failed / suspected item list
- Input completeness note (how much evidence rcheck filled in itself)
- Review score + key issues
- Mark the summary start and end with a separator

[Review report output — the subagent must do this itself]
After the review, write the full report to the path specified by the main agent; if the subagent cannot write files, return the full report text verbatim to the main session, which writes it verbatim and notes in the report that the write was delegated.
```

## Key emphasis

- **rcheck only verifies and never modifies**; the main agent fixes discovered problems
- **Input tolerance**: it can run on anything and fills in incomplete evidence itself
- **The reconciliation table is the core**; every row must have evidence rcheck produced itself
- Only ≥4 passes
- 1 first review + ≤2 re-reviews; beyond the limit, decide by the current score

## Review flow

Main session flow:
1. Complete the task and write the implementation summary
2. Use the `task` tool to delegate a subagent with: implementation summary path + original plan path (if any) + report output path (the payload contains the full review prompt above)
3. On receiving the report (read the GATE marker branch, continue automatically):
   - PASS → append a `## rcheck response ({report identifier})` section at the end of the implementation summary and respond to every ⚠️ item one by one: mark 【accepted】 (explain exactly what changed) / 【not accepted】 (explain why); skipping any item is not allowed → report to the user based on the review conclusion, including a one-sentence summary (e.g. "rcheck reported N ⚠️: X accepted, Y not accepted, see the response at the end of the document")
   - FAIL → fix the implementation (note that rcheck will not change it for you) → append a `## Round N fixes` section at the end of the implementation summary (which FAIL items were changed, what changed, which files, which verification commands were re-run) → delegate one more subagent re-review
4. At most 3 times (1 first review + ≤2 re-reviews); beyond the limit, decide by the current score and escalate to the user
