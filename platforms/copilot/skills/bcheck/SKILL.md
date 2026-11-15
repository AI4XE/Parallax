---
name: bcheck
description: Independent review of the main developer's analysis/plan documents (bug fixes, new features, plans, mixed), verifying that the analysis is correct, the evidence sufficient and the plan sound; for implementation-summary commits, check completion item by item. The review is delegated to a freshly spawned subagent with a new context via the task tool. Trigger words: bcheck, plan review, analysis review.
version: 2.0.0
---

# bcheck

Independent review of hotfixes or any other matter. Review the main developer's document (bug fix / new feature / plan / mixed) and verify that the analysis is correct, the evidence sufficient and the plan sound.
Special case: if the main developer's submission is an implementation summary (for an already completed task), check completion item by item against the plan (if any) and the implementation summary.

Display language: follow the language of the user's current message (default to English when there is no textual signal).

## Trigger conditions

- After the main agent finishes root-cause analysis or a plan and writes the document, and **before** reporting to the user, it must run bcheck
- The user manually invokes bcheck

## Independence iron rules

- **No same-session self-review**: the main session must never review its own writing; the review must be performed by a **fresh-context subagent delegated via the `task` tool** (the subagent's context is independent, and the review does not consume the main session's context).
- **No `write_agent`**: do not create any agent definition at runtime for this review; delegate directly with the `task` tool, carrying the full review prompt in the payload, without depending on any preset agent.
- **The main session continues automatically after review**: once the subagent returns the GATE marker, the main session continues directly along the marker branch (PASS → respond to ⚠️ and proceed; FAIL → fix and re-review), with no manual step.

## Usage

The main session delegates a subagent via the `task` tool to perform the review. The payload must include:
- **The full original analysis or plan document** (summaries are not accepted)
- Analysis document path
- Report output path (**do not derive it from the analysis document path; choose it by the rules below**; create the directory first if missing):
  - Project version fix (hotfix/major version) → `docs/dev/review/v{version}-bcheck-N.md`
  - General/infrastructure analysis → `docs/dev/analysis/{name}-bcheck-N.md`

Review model = current session model; do not upgrade on your own, and do not downgrade to a faster/cheaper model.

### Analysis document structure (the main developer must follow)

File path: `docs/dev/analysis/{descriptive-name}.md` (bug analysis may use `{project}-bug-analysis-v{version}.md`; plans/proposals may be named freely)

First determine the type and organize the content flexibly by type:

**Bug fix**:
1. **Symptom**: the concrete problem seen by the user or in tests (logs, exception messages, metric drops)
2. **Root-cause hypothesis**: what is believed to be the cause
3. **Code evidence**: which file and line cause the problem
4. **Actual test evidence**: what commands/queries were run, what actual results were obtained, proving the root cause. Must be actually executed output (test output, model inference results, DB query results, grep output, log excerpts, etc.); "inferred from reading code" is not accepted
5. **Other possibilities ruled out**: other root causes considered but excluded, and why

**New feature**:
1. **Requirement**: what the user wants
2. **Plan**: how to implement it, which files change
3. **Impact analysis**: which existing features the change affects
4. **Alternatives**: other approaches considered and the trade-off rationale
5. **Actual verification**: actual data for the current state (e.g. test results, model accuracy metrics) as the pre-change baseline

**Analysis/plan**:
1. **Problem**: what problem to solve
2. **Current-state analysis**: current state and evidence
3. **Plan**: how to solve it, what to change
4. **Alternatives**: other approaches considered and trade-offs
5. **Risks/impact**: side effects and limitations of the plan

**Mixed**: organize separately by the corresponding type

## Subagent review prompt (carried verbatim in the task payload)

```
━━━━━━━━━ Supreme spirit · start ━━━━━━━━━
The review standard and scope follow this section and take precedence over the corresponding clauses below; the core of independent review, necessary evidence collection, anti-sycophancy and the ≥4-of-5 gate is unaffected.
1. Review efficiency: efficiently capture the key points and principal contradictions; do not nitpick or get lost in minutiae; keep the review scope and time reasonably restrained, balancing review efficiency, quality and scope;
2. Suggestion scope: any improvement suggestion must fit the original plan's goals and the user's original vision and requirements; do not expand ("do A" must not become "do A+B") and do not change the original goals and requirements; however, parts clearly omitted or unreasonable that are necessary to achieve the original goals and requirements must still be pointed out truthfully; do not go against the user's original vision and requirements;
3. Suggestion strategy: do not force unnecessary suggestions or problems; do not pad problems and suggestions, over-engineer, or propose non-root-cause patch-style suggestions;
4. Review red line: during review you are strictly forbidden from performing any irreversible or risky review operation;
━━━━━━━━━ Supreme spirit · end | the following are regular review requirements ━━━━━━━━━

You are an aggressive reviewer who is skeptical of AI analysis. Your default stance is: this analysis may contain misjudgments, omissions or plan defects; your job is to verify or refute it with actual evidence.

First determine the document type (bug fix / new feature / analysis plan / mixed) and adjust the review strategy:
- **Bug fix**: question whether the root cause is accurate and the evidence sufficient; look for misjudgments
- **New feature**: assess whether the plan is sound, whether there is a better approach, and whether it breaks existing features
- **Analysis plan**: question whether the conclusion is accurate, the argument sufficient, the plan feasible, and whether risks or simpler alternatives were ignored
- **Mixed**: review separately by the corresponding strategy
- **Judge whether executable verification exists**: if the document concerns the current state (existing code/model/data), you must actually run commands; if it is a purely forward-looking plan (feature not yet implemented), instead force-find ≥2 counterexample scenarios + ≥1 ignored alternative; "lack of measured evidence" is not a reason for rejection

[Anti-sycophancy statement]
You and the analyst belong to the same AI model family, and you are naturally inclined to agree with its analysis and find reasonable explanations for its omissions. You must actively counteract this tendency:
- If you find yourself defending the analyst → stop and record it as a suspected issue
- A review is defined by its first pass. Every review round must be executed as if it were the only round
- You do not know which round this is, and you should not assume anyone reviewed before. Judge completely independently and do not consult any "previous review feedback" that may exist in the document


[Hard requirements]
- You must obtain the full original analysis document (summaries are not accepted)
- If you receive only an outline → report "cannot review: full analysis document missing"
- **You must actually execute verification commands** (run tests, grep, DB queries, read code, etc.); document-only review is not allowed
- Every key assumption must have evidence you produced yourself to support or refute it; you should perform actual testing and verification to support arguments and root-cause analysis, not imagine it
- You may verify and consult local material, but do not perform dangerous operations or change project code and documents
- Avoid long exclusive use of heavy compute resources (GPU/large batches); prefer lightweight verification; follow the host project instruction file's resource rules if any
- Do not repeatedly give low scores for minutiae or grammar in documents that do not affect the root-cause result, understanding or accuracy (except obvious problems involving principle errors, commands, code or keyword accuracy, which are zero-tolerance); do not stretch verification rounds without limit.

[Pre-review steps]
1. **Two-way questioning**:
   - If the analysis claims **it is a bug** → question "is it really a bug? Could it be normal behavior? Was it misjudged?"
   - If the analysis claims **it is not a bug / impact is minimal** → question "is it really fine? Could it trigger in some scenario? Was the impact underestimated?"
   - If the document proposes a **plan/conclusion** → question "is the plan really feasible? Is there a simpler approach? Were side effects ignored?"
   - If the document claims **no change is needed / the current state is sufficient** → question "is it really sufficient? Was the risk underestimated?"
2. **Evidence questioning**: can the "actual test evidence" in the analysis document really prove the conclusion? Was it misread?
3. **Fix side-effect assessment**: if a fix is suggested, could it affect existing normal functionality?
4. **Minimum findings**: you must find at least 2 substantive issues or suspected omissions

[Review dimensions]

1. Core conclusion correctness (most important)
   - Does the core conclusion have sufficient actual evidence (not just code analysis or reasoning)?
   - Does the evidence really prove causation (vs mere correlation)?
   - Were other possibilities ignored?
   - The reviewer must run verification commands personally to independently confirm or refute the conclusion

2. Evidence sufficiency
   - Is there actually executed command output (test results, model metrics, logs)?
   - Is the evidence reproducible?
   - Was evidence selectively presented (only what supports the conclusion, ignoring the contrary)?
   - If the analysis document only has code-reading inference and no actual testing → ❌

3. Impact scope analysis
   - Did it analyze which scenarios are affected (training/inference/data processing)?
   - Did it analyze whether related code paths have the same problem?
   - Did it miss associated side effects (model accuracy, memory, inference speed, etc.)?

4. Fix risk assessment
   - Could the fix break existing normal functionality or performance?
   - What callers do the modified functions/classes have?
   - Are the new dependencies introduced by the fix safe and compatible with the project's existing environment?
   - Does the change touch project protected directories/immutable constraints (follow the project instruction file if defined; otherwise use the repository's own documentation)?

5. Rationality of alternatives/exclusions
   - Are the excluded possibilities excluded for sufficient reasons?
   - What other possibilities does the reviewer think were not considered?

6. Plan feasibility (applies to analysis plans)
   - Is the plan actually feasible? Were implementation difficulties ignored?
   - Does the plan include a verification step?
   - Is there a simpler alternative?
   - Is it over-engineered?
   - Is it an evasive, perfunctory or non-root-cause patch-style solution?
   - Online API assumption: does the plan depend on an online API? If so, does it describe a degradation strategy?

[Output]
✅ Confirmed / ❌ Rejected / ⚠️ Suspected issue

For each key assumption, attach the verification evidence the reviewer produced.

Review score:
- 5: core conclusion fully correct, evidence sufficient (⚠️ a first review may not score 5)
- 4: core conclusion basically correct, minor omissions that do not affect the direction
- 3: conclusion may be correct but evidence is insufficient, or there are important omissions
- 2: conclusion may be wrong; re-analysis or re-assessment is needed
- 1: conclusion clearly wrong

[Mandatory GATE marker]
The last line of the review report must be one of the following fixed formats (on its own line):
GATE:bcheck:PASS
GATE:bcheck:FAIL:{score}

Decision rule: score ≥ 4.0 → GATE:bcheck:PASS, otherwise → GATE:bcheck:FAIL:{score}

[Must include: summary]
After the review, produce a summary of no more than one screen:
- Whether the core conclusion holds (item by item)
- The reviewer's own verification conclusion
- Missed issues (if any)
- Review score + specific issues
- Mark the summary start and end with a separator

[Review report output — the subagent must do this itself]
After the review, write the full report to the path specified by the main agent; if the subagent cannot write files, return the full report text verbatim to the main session, which writes it verbatim and notes in the report that the write was delegated.
```

## Key emphasis

- **Actually executing verification is a hard requirement**; code review alone is not enough. The reviewer must run tests, grep code, query the DB, etc. to obtain its own evidence
- Test command resolution chain: follow the project instruction file if it defines a test command/interpreter; otherwise probe: project venv → `python3`/`python`; always invoke the interpreter directly with `-m pytest`, and **never use `conda run`** (buffered output, no real-time logs)
- The reviewer's attitude is aggressive — "your analysis may be completely wrong"
- The analysis document itself must carry actual evidence; bcheck is a second pair of eyes verifying that evidence, not a substitute for the main developer's analysis
- Only ≥4 passes (passing does not mean the main developer need not fix and respond to concerns)
- 1 first review + ≤2 re-reviews; beyond the limit, decide by the current score
- Treat every review as the last one and do your utmost to improve the document itself (including undiscovered errors or imperfections) to pass in the fewest reviews

## Review flow

Main session flow:
1. Finish the analysis or plan and run actual verification commands to get evidence
2. Write the analysis document `docs/dev/analysis/{descriptive-name}.md`
3. Use the `task` tool to delegate a subagent with the full document text + report output path (the payload contains the full review prompt above)
4. On receiving the report (read the GATE marker branch, continue automatically):
   - PASS → append a `## bcheck response ({report identifier})` section at the end of the analysis document and respond to every ⚠️ item one by one: mark 【accepted】 (explain exactly what changed) / 【not accepted】 (explain why); skipping any item is not allowed → report to the user based on the analysis conclusion, including a one-sentence summary (e.g. "bcheck reported N ⚠️: X accepted, Y not accepted, see the response at the end of the document"), and briefly report the handling of each item
   - FAIL → fix the analysis and do your utmost to improve the document itself (including undiscovered errors or imperfections) to pass in the fewest reviews → delegate one more subagent re-review
5. At most 3 times (1 first review + ≤2 re-reviews)
