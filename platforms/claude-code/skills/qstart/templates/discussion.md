# {Infra improvement discussion — timestamp / Hotfix discussion — v{target-version} / Development discussion — type to be decided}

## Collaboration mode (agreed for this run)
- Save the user time and effort; concise plain-language exchanges; check facts yourself and concentrate judgment in the discussion.
- Intake: record cheaply only; wait until the user explicitly finishes sending; no structuring, no plan, no research.
- Research: concentrate necessary research, flexibly arrange background research (e.g. subagents), and actually collect the results.
- Alignment: one question at a time, 2–4 options with a recommendation and reasons; after the round, do additional focused research together; multiple rounds allowed; proactively drive to a complete overview.
- grill-me: on by default; verify facts and ask questions along decision dependencies; can be turned off verbally without changing the three-stage rhythm.
- HARD-GATE: before an explicit qgo or hand-off, do not write a formal root-cause/plan, do not enter bcheck, do not implement.
- Follow-up: qgo → bcheck → gclear → a clean session takes over execution; the executor does real verification → rcheck → scoped commit → xwrap → final receipt. infra does not bump versions; hotfix updates the actual version source; solo (default tier) has no orchestrator after gclear, and the hand-off session works straight through the executor checklist to the receipt; the team tier runs the full orchestrator + separate executor chain (headless stream-json, launched by executor_driver).
- This file keeps only requirements, research and decisions; gate material is written separately, with existing content referenced by anchor.

## Identification result for this run
- Development mode: {infra non-version / hotfix version / to be decided from the full requirement; explicit or inferred}
- Execution tier: {solo / team; solo by default, only explicit team runs the full orchestrator + executor chain}
- Target project: {host / sub-project / independent tag}
- host / Git root: {actual root}
- task root: {actual task directory}
- Document ownership: {host or project existing convention}
- Timestamp / target version: {identified live; do not invent when undecided}
- Version source and sync points: {applies to hotfix}
- Commit prefix: {infra=[infra]; hotfix=project convention}
- Source channel: {actual source for this round}
- grill-me: {on / off}

## Special notes for this run
{appended instructions; none by default}

## Requirement intake
{record item by item; note the actual signal that the user finished sending}

## Research record
{collected facts, evidence anchors, open questions and exceptions}

## Alignment and decisions
{round-by-round questions, options, recommendation rationale and user decisions}

## Big-picture understanding
{explicit type/version, goals, scope/non-goals, decisions, acceptance, risks and exact anchors; only Q-Go Ready for qgo, which does not mean hand-off}
