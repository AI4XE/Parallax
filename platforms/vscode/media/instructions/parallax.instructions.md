---
description: Parallax development flow (suite instructions)
applyTo: "**"
---
# Parallax development flow (suite global instructions)

This VS Code installation has the Parallax lightweight development flow suite available (bundled with the Parallax extension, solo direct-execution tier): structured discussion → reviewed plan → independent review gate → automatic hand-off to a clean session → reconciliation wrap-up. The seven skills (including wish) are provided by this extension and can be invoked explicitly (`/skill-name`) or triggered automatically from natural language.

## The flow in one sentence

qstart (discussion alignment) → the user says qgo → plan + independent bcheck review (PASS≥4) → gclear automatic hand-off to a clean session → hand-off session executes + real verification → rcheck reconciliation (PASS≥4) → scoped local commit → xwrap wrap-up.

## Skill trigger words

| Skill | Trigger phrases (examples) |
|---|---|
| qstart | "qstart", "Q development discussion", "start a discussion" (aliases ver/version→version task, nover→non-version task) |
| qgo | "qgo", "Q hand-off development", "the direction is fine, hand it off" |
| bcheck | "bcheck", "plan review", "analysis review" |
| gclear | "gclear", "clear and hand off", "hand off to a new session", "cross-session context hand-off" |
| rcheck | "rcheck", "reconciliation review", "wrap-up review" |
| xwrap | "xwrap", "wrap up", "wrap up now" |
| wish | "wish", "许愿创造", "许愿编程", "Parallax" |

## GATE marker semantics

- `GATE:bcheck:PASS` / `GATE:bcheck:FAIL:{score}`: plan review gate; only a score ≥4 passes.
- `GATE:rcheck:PASS` / `GATE:rcheck:FAIL:{score}`: implementation reconciliation gate; same as above.
- `HANDOFF-ACK:<token>`: gclear hand-off receipt — the new session's first reply after receiving the hand-off prompt must carry this marker (the token matches the hand-off material).
- Gate reviews are always performed by an independent-context subagent (bcheck/rcheck delegate via task); same-session self-review is forbidden; `write_agent` is not used to create agents at runtime.

## Artifact locations

Discussion → `docs/dev/infra/` or `docs/dev/hotfix/`; analysis/plan → `docs/dev/analysis/`; review reports → `docs/dev/review/` or `docs/dev/analysis/`; hand-off material → `docs/dev/handoff/`. Follow the project's existing document conventions if any.

## Recommended setting (one-time, optional)

The skill chain's automatic hand-off (gclear) and automatic review depend on the session tools and delegation tool. It is recommended to set the chat permission tier to **zero-confirmation fully automatic** (setting key `chat.defaultConfiguration`, choose Bypass Approvals or Autopilot) — cross-session messages and delegation then have no pop-ups and the whole chain needs no manual steps. The default Manual tier also works: each tool's first call is approved once and same-kind calls in the same session are then auto-exempt, with just a few more clicks.
