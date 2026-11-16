---
name: qstart
description: "Wish / Parallax — development discussion entry: intake → focused research → round-by-round alignment → complete overview, then wait for explicit qgo once Q-Go Ready. Chinese triggers: 许愿创造, 许愿编程. Supports infra (non-version) and hotfix (version) tasks; bare qstart infers from the full requirement, not infra by default. Default tier: solo direct execution; team enables orchestrator + executor; aliases ver/version/nover. Trigger words: qstart, Q development discussion."
user_invocable: true
portable: true
version: 2.0.1
display_lang: en
---

# qstart — Development discussion

The goal is to save the user time and effort: establish the facts first, then discuss judgment, and proactively drive to a complete "big-picture understanding" through concise, plain-language exchanges; then wait for the user's qgo or an explicit hand-off. A finished discussion or an accepted option is not execution authorization.

This skill ships its own full discussion protocol and `templates/discussion.md`; do not read or invoke other discussion/hand-off skills to fill in the logic. The later qgo stage reuses shared capabilities such as bcheck, gclear, rcheck, and xwrap; this skill creates no locks or workflow state machine. Before use, check whether the skill has an applicable `local/LOCAL.md`; if so, reuse and adapt it first.

## 1. Identification and cheap recording

- Use the current Git root as the root directory; in a non-Git environment use the current directory. Resolve paths relative to the root and do not assume a sub-project layout. Distinguish host/Git root, task root, document directory, actual version source and sync points, and commit prefix. Without a project name, use the current project; with a name, locate it yourself. When ambiguous, ask a single question first with a sensible default; never silently guess the project.
- `infra` means a non-version task; `hotfix` means a version task. Explicit parameters and the user's latest explicit instruction take precedence. A bare qstart decides from the full requirement and project conventions; do not default to infra and do not repeatedly ask for the type during intake. When information is insufficient, mark it as "to be decided from the full requirement". Map aliases by word, order-independent: `ver`/`version` → hotfix, `nover` → infra, `team` → full execution-tier chain (see next item). Always persist and document the canonical names (infra/hotfix, solo/team).
- Execution tier is **solo by default** (lightweight direct execution, no orchestrator/separate executor). Only the explicit keyword `team` uses the full orchestrator + separate executor chain: `team` appearing anywhere in the invocation arguments (mode and tier are orthogonal; order is irrelevant), or the user explicitly saying team before the hand-off; a verbal switch mid-session after a bare qstart start is equally valid (window = before the qgo hand-off). The literal `solo` is retained as an "explicit default declaration" and is harmless. Write the tier into "Identification result" for qgo.
- During intake, only record cheaply; do not conduct deep investigation to identify the project/version. Before the task type is settled, record in a temp directory permitted by the host; once identification is sufficient, persist to the path below. When migrating, keep the original intake content and update references; do not create a parallel document system.
- The first receipt briefly lists: mode (explicit/to be decided), infra timestamp or the known hotfix target version, target project, execution tier (solo/team, default solo), `grill-me: on/off` (default on), and the current record path. If the type is undecided, do not invent a version. Confirm the final identification once in the overview so the user can correct it.
- Initialize the record with `templates/discussion.md` from this directory. Default paths once the type is determined: infra → host `docs/dev/infra/infra-discussion-{timestamp}.md`; hotfix → host `docs/dev/hotfix/v{target-version}-discussion.md`. If the target project has an explicit document convention, follow it and record accurate anchors.
- Probe the actual hotfix version source inside the task root (e.g. pyproject.toml, package.json and their sync points); the target defaults to PATCH+1, and an explicit user instruction takes precedence. For independent tag tasks without a real version, use "tag-timestamp" and do not invent a version source. Resolve uncertain versions during alignment.
- If the file exists, first verify ownership: continue the same task; preserve other tasks' files; choose a project/task suffix that still matches the original naming glob and record the exact path; never treat the same version alone as the same task. Create missing directories.
- Put personalized instructions appended at the call tail or during the process into "Special notes for this run"; they stack on top of the core protocol and do not replace stage gates.
- Reply along the message source: cc-connect normal replies are delivered by the bridge, do not send again; Telegram channel messages use the reply tool available this round with the original chat_id; otherwise use normal text. Do not duplicate across channels. Display language priority: trigger-message language > explicit user instruction (e.g. "use English" takes effect immediately) > the frontmatter `display_lang` field default; for a bare command with no language signal, read this file's frontmatter `display_lang` value before replying and use it as the default display language.

## 2. Three-stage protocol

### Intake

For each message, only record and briefly acknowledge — **no structuring, no plan, no deep dive, no research** — until the user explicitly signals the end, e.g. 「没有了」 (a typical Chinese end-of-input signal; judge by semantics in general and do not add equivalents for other languages). Invoking qstart itself is not an end signal. If the user has explicitly finished, do not ask again.

### Focused research

Briefly announce that research is starting. The main session orchestrates, concentrates necessary research, and may flexibly arrange background research (e.g. subagents); enough depth to judge the direction is sufficient. Research tasks must have real result collection and exception handling; do not just launch them and assume completion. Record facts, evidence, open questions, and necessary anchors in the discussion record.

- During research, run one cheap check for `git` (e.g. `git --version`). If Git is missing, raise it as one alignment question: state that without Git follow-up tracking and saving may be affected and ask whether the user wants to install it; do not block and do not check at trigger time.

Resolve facts that can be found from code/files/live services yourself; reserve preferences and value judgments for alignment. Do not open a formal plan or enter bcheck. Do not dump a question list on the user.

### Round-by-round alignment

- Ask one question at a time, with 2–4 options, a clear recommendation, and a brief reason; plain and concise, no long essays; the user controls the pace. Prefer text-based multiple choice; do not default to pop-ups.
- In each round, work through the questions that need user judgment in dependency order; do not switch to fragmented research after each answer. When new facts are needed, first collect the round's questions, research them together, then move to the next round; multiple rounds are allowed.
- grill-me is on by default: check judgments systematically along an upstream→downstream decision tree, resolve dependencies first, and do not ask the user about branches you can check yourself. Do not interrogate for its own sake or sacrifice the time-saving goal.
- If the user verbally asks to turn grill-me off, turn it off and record it; still research normally and align thoroughly without lowering quality; do not add a toggle file.
- Decide technical details that need no user choice yourself; do not invent questions to fill rounds. Do not stop mid-way without reason and make the user prod you to continue.

## 3. Complete overview and hand-off boundary

Once research and round-by-round alignment are sufficient, proactively present the complete "big-picture understanding" and write it into the record:

- Task type is explicitly infra (non-version) or hotfix (version); for hotfix give the actual target version, version source and sync points.
- Goals, scope and non-goals, key decisions, completion criteria, open risks, personalized notes.
- host/Git root, task root, document anchors and commit ownership; a single project may be described together without repeating paths.
- The next step is only **qgo or an explicit user hand-off**, i.e. **Q-Go Ready**, not reviewed or executed. The overview does not replace prior user alignment.

**HARD-GATE: before the user explicitly says qgo / "the direction is fine, hand it off", do not write a formal root-cause/plan document, do not enter bcheck, and do not touch code or deploy.** A finished discussion, no new questions, an accepted option, or choosing the Q route does not cross the gate. Wait for authorization after the overview; if the user corrects something, update the discussion and overview and do not silently switch type. After an explicit hand-off, hand over to qgo without asking the user to confirm again.
