---
name: xwrap
description: Parallax development flow · wrap-up skill (optional). Codifies the "wrap up" prompt: inspect the repository's current state → report obvious problems/unfinished parts → update todo (infra: itodo / hotfix: itodo+btodo; for heterogeneous projects detect their actual todo files, and use the built-in lightweight three-category template when the format is missing) → write wrapup → do not change code during wrap-up → intelligent autonomous git commit (auto-commit this run's artifacts scoped; for code/ambiguous cases ask the user). No state dependency; determines mode/version live at runtime. Trigger words: xwrap, wrap-up, wrap, wrap up.
whenToUse: The user wants to wrap up a development task (update todo, write wrapup, tidy commits), or explicitly mentions xwrap / wrap-up / wrap.
user-invocable: true
metadata:
  portable: true
  version: 2.0.0
  display_lang: en
---

# xwrap — Wrap-up (optional)

**Purpose**: codify the prompt the user used to paste at every wrap-up — "do the necessary wrap-up, report obvious problems, update todo, write wrapup, intelligent git commit" — into a skill. **Optional step** (skip when the user is low on tokens / the docs are sufficient / forgotten / deemed unnecessary).

**No state dependency** — **inspect the repository's current state at runtime** for the wrap-up (the way the user has always wrapped manually, without problems); do not read any workflow state file.

**Generic / self-contained**: the prompt and templates are fully embedded and **do not depend on a host rule document**. Paths are relative to repo-root (`git rev-parse --show-toplevel`, falling back to `$PWD` on failure). SKILL.md contains **no** project name / env / chat_id / session name / absolute paths.

**Channel-agnostic**: if this round's message comes from an instant-messaging integration, reply along the original channel; otherwise output normal session text.

**Display language**: display language priority: trigger-message language > explicit user instruction (e.g. "use English" takes effect immediately) > the frontmatter `metadata.display_lang` default; for a bare command with no language signal, use it as the default display language.

**Optional personalization**: text appended at the call tail/during the process → stack and follow; empty by default.

---

## Execution steps (the main agent runs these in order after invocation)

### Step 1 — Determine mode / version (live at runtime)

Determine mode live from the most recent Parallax record file (`docs/dev/infra/infra-discussion-*.md` or `docs/dev/hotfix/v*-discussion.md`) + **the repository's current state**.
- **hotfix version** takes the **repository's current latest version** (read from the version file, avoiding multi-batch naming drift — multi-batch wrapups are named after the **last batch's version number**).
- Ambiguous → ask one question.

```bash
# bash (Linux / macOS)
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
STAMP=$(date +%Y-%m-%d-%H-%M)
git -C "$ROOT" status --short; git -C "$ROOT" log --oneline -10
```

```powershell
# PowerShell (Windows native, no bash)
$ROOT = git rev-parse --show-toplevel; if (-not $ROOT) { $ROOT = (Get-Location).Path }
$STAMP = Get-Date -Format "yyyy-MM-dd-HH-mm"
git -C "$ROOT" status --short; git -C "$ROOT" log --oneline -10
```

### Step 2 — Necessary wrap-up (if any)

Only do the necessary wrap-up tidying; **do not change project code** (see Step 6).

### Step 3 — Report obvious problems

Tell the user whether this development has **obvious problems** or unfinished parts.
- **"Obvious problem"** = a problem that is **still unresolved** and **clearly affects functionality** (hotfix: version functionality) or subsequent development.
- **Already resolved / irrelevant / out of scope for this task** → **do not report**.
- **⚠️ Exception — outstanding verification (delivered but unverified) must be reported (exempt from the "do not report" rule)**: if this run has functionality that was **delivered but self-declared verification was not truly achieved** (**even if it is off by default / does not currently affect functionality**), it must be listed **separately at the top** of the "obvious problems" section: `outstanding verification: {feature} — what verification is missing — which approaches were tried/why they failed`. **Do not** classify it as a non-blocking omission because "off by default = no functional impact", or place it alongside presentation/copy blemishes; also update the todo and name it to the user in the receipt.

### Step 4 — Update todo (by actual state; check and update each item's status)

- **infra** → `docs/dev/todo/itodo.md`
- **hotfix** → `docs/dev/todo/itodo.md` (infra-related issues) + `docs/dev/todo/btodo.md` (**main code bugs / data issues / code defects**)
  - Classification: main code bugs → btodo; infrastructure/scripts/process/toolchain → itodo.
- **Heterogeneous projects**: detect the project's **actual todo files** (do not hardcode paths; some projects use `now/tasks.md` etc.); if unsure → ask once on the first run.
- **Format (lightweight)**:
  - If the target project has **no todo format/file** → create one with the built-in **lightweight three-category template** below: three top-level sections **`## To do` / `## Not planned` / `## Done`** + a priority marker before each item title **🔴 P0 / 🟠 P1 / 🟡 P2 / 🟢 P3** (this format is a generic lightweight convention and needs no specific project file).
  - **Existing document** → **adapt to its existing format**; do not force a rewrite.
- Check and update each item's "to do / done" status; record newly discovered issues in the corresponding file and **do not omit them**.

### Step 5 — Write wrapup

- **infra** → `$ROOT/docs/dev/general/general-wrapup-${STAMP}.md`
- **hotfix** → `$ROOT/docs/dev/hotfix/v{version}-wrapup.md` (version = the repository's latest)

Create the directory with `mkdir -p` if missing. Content: wrap-up conclusion + anything worth recording (see the template below).

### Step 6 — Do not change project code during wrap-up

During the wrap-up phase **do not change project code** — unless you ask the user and get **explicit approval**.

### Step 7 — Intelligent git commit (autonomous judgment, fewer second confirmations)

Wrap-up artifacts are by default **auto-committed scoped by this session**; do not make the user re-send "commit" every time. The rules and red lines are below.

**7.1 Automatic scoped commit (this run's task artifacts · high-confidence allowlist)**
Identify "this run's task artifacts" primarily by **files actually edited/written by this session**, plus two fallbacks:
- (Fixed-path fallback) the wrapup file written in Step 5 and the todo files updated in Step 4 (itodo/btodo, non-empty diff) — paths known, certainly this run's artifacts;
- (Namespace narrowing check · **runtime-detected · never hardcoded**) documents that fall in the **document locations determined at runtime by this session** and were indeed produced by this session: i.e. the directory/same naming prefix of the record file read in Step 1, the todo file detected in Step 4, and the directory of the wrapup written in Step 5. The namespace is **derived from these runtime detections and never hardcodes any project-specific directory** (typical projects use `docs/dev/{infra,hotfix,general,analysis}/`; heterogeneous projects use their actual todo/document directory such as `now/` — **whatever this session actually detected in Step 1/4 governs**; the above are examples, not an allowlist).
On an allowlist hit → `git add <each exact file>` (**never `git add -A` / `git add .`**) → commit.

**7.2 Code changes → do not auto-commit; prompt the user**
There should be no uncommitted code during the wrap-up phase (the main development chain commits first). If the working tree has **uncommitted code/script files** (`*.py` and other source paths, e.g. `src/`, `scripts/`, package directories, i.e. non-document paths) → **exclude them from the automatic commit** and explicitly tell the user "uncommitted code changes found; the upstream gate may have missed a commit; please confirm how to handle them", leaving the decision to the user.

**7.3 Never auto-include**
Dirty state this session never touched (the majority), runtime/config side effects (data directories, `*.db`, `auth_state`/`users_config`, etc.), any deletion (D) entry, secret-sensitive names (`.env`, `*credential*`, `*.key`, `*.pem`, etc.) — all excluded from automatic commits; even if this run "incidentally" touched secret-like files, ask first.

**7.4 Ambiguous → list once and ask the user (not file by file)**
The following are ambiguous and **should be collected into one list and asked once** (avoid fragmentation): paths outside the known namespace, session memory uncertain whether it changed them this run (e.g. after mid-way context compaction/reset), sensitive names, deletion entries, DB/binary/large artifacts, discovered uncommitted code, multiple unrelated concerns mixed and hard to cleanly separate.
**Zero-memory special case**: if this session's memory is **completely empty/unavailable** (after context compaction/reset or a cross-session proxy restart → cannot confirm which files this run changed), **do not silently commit only the fixed-path fallbacks** (that would miss newly written discussion/analysis, etc.) → you must present the whole "candidate list of this run's artifacts inferred from the runtime-detected namespace + recent changes (e.g. untracked/modified entries under that namespace in `git status`)" to the user for confirmation; better to ask once more than to silently miss a commit.

**7.5 Safety red lines (always apply)**
- Never `git add -A` / `git add .`; add only exactly enumerated files.
- Never push / pull / configure a remote (local git only).
- Correct commit prefix: infra=`[infra]` (no project name) / hotfix=`[{project}] v{version}: …`; commits carry no AI attribution / `Co-Authored-By` / `Generated with` / AI email marker (user rule).
- **Echo the exact file list about to be committed to the user before adding** (on the corresponding receipt channel), and **report the commit hash after committing** (transparent reporting; never report only the good news).
- Do not cause any side effects on other dirty state (no `git clean` / `checkout --` / stashing others' changes).
- If there is nothing to commit this run (only unrelated historical dirty state) → truthfully report "no changes from this task to commit" and do not force a commit.

> **Cross-project generic**: all of the above relies only on `git -C $ROOT` + session memory + relative namespace rules, with no fixed paths/hardcoding. For multi-project repositories without a root-level version file: use `[infra]` for infra commits; for hotfix, infer the version from the **sub-project's actual version source** and, if unsure, route to 7.4 and ask the user.

---

## Six fixed templates (use one per wrap)

### Template wrap-infra (infra wrap-up prompt essentials)

> Do the necessary wrap-up (if needed), then tell me: does this development have any **obvious problems** or unfinished parts? "Obvious problem" = an unresolved problem that clearly affects functionality or subsequent development. Already resolved / irrelevant / out of scope are not reported. Update `docs/dev/todo/itodo.md` by actual state and check/update each item's resolved/unresolved status. Write the wrap-up conclusion and anything worth recording to `docs/dev/general/general-wrapup-{timestamp}.md`. **Do not change project code during wrap-up** (unless you ask me and get explicit approval). Finally **intelligently complete the local git commit autonomously** (commit this run's artifacts scoped; list code/ambiguous items and ask me).

### Template wrap-hotfix (hotfix wrap-up prompt essentials)

> Same as wrap-infra, except: obvious problems mean those affecting **"version functionality"** or subsequent development; update both `docs/dev/todo/itodo.md` **and `btodo.md`** (main code bugs→btodo, infra-related→itodo); write the wrap-up conclusion to `docs/dev/hotfix/v{version}-wrapup.md` (version inferred from the repository's current latest).

### wrapup file skeleton

```markdown
# {infra topic / v{version}} Wrapup — {timestamp}

## What was done this run
(completed items, one per line)

## Obvious problems / unfinished
(unresolved and clearly affecting functionality/subsequent development; write "none" if none)

## todo updates
(itodo / btodo item numbers + one sentence)

## Worth recording
(key decisions / workarounds / follow-up suggestions)
```

---

## Key notes

- **No state dependency**: inspect the repository at runtime; do not read flow-specific state files.
- **Multi-batch versions**: name hotfix wrapups after the **last batch's** version number (take the repository's latest version).
- **Report only obvious problems**: resolved/irrelevant/out-of-scope ones are not reported, avoiding noise.
- **Do not change code during wrap-up**: unless explicitly approved.
- **Intelligent git (Step 7)**: auto-commit this run's artifacts scoped (never `add -A`/push/remote; correct prefix + no AI attribution), **echo the list before adding and report the hash after committing**; do not auto-commit uncommitted code and ambiguous items — list them once and ask the user; if there are no artifacts from this run, truthfully report "nothing to commit".
- **Transparency**: unfinished parts are as prominent as completed items; do not conceal.
