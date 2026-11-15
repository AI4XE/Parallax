![Parallax](assets/banner.svg)

Parallax is a lightweight enhancement layer on top of your coding-agent harness. You own the goal; the agents own the work, context management, and independent acceptance.

Install Parallax, then just wish it. **Make it real.** Say what you want, make a few choices, hand it off — an agent team does the work under independent review and in a clean session, audits the result, and commits it clean.

[中文文档](README.zh-CN.md)

[![Claude Code](https://img.shields.io/badge/Claude_Code-Install-6B4FBB?logo=anthropic&logoColor=white)](#claude-code)
[![DeepSeek Harness](https://img.shields.io/badge/DeepSeek_Harness-Install-4D6BFE?logo=deepseek&logoColor=white)](#deepseek-harness)
[![VS Code Copilot](https://img.shields.io/badge/VS_Code_Copilot-Install-0098FF?logo=githubcopilot&logoColor=white)](#copilot)
[![License](https://img.shields.io/github/license/AI4XE/Parallax)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/AI4XE/Parallax)](https://github.com/AI4XE/Parallax)

> The three install badges jump to the install section of this README — they are not one-click marketplace installs.

## Three steps

> **Plugin trigger words**: `wish` · `Parallax` · `qstart`

1. **Wish** — say what you would like, as casually as you like. You do not need to have it figured out; add more in several messages and say "that's all" when you are done.
2. **Choose** — the agent researches first, then aligns with you one question at a time; you just pick.
3. **Hand off** — the agent team takes it from there: plan → independent review → new-session execution → independent audit → scoped commit → wrap-up.

> **Two modes**
> - **solo** (default): you work directly with the execution session; the current session is the executor. Suited to everyday use.
> - **team**: your conversation always stays with the orchestrator while the executor runs in its own session. Suited to large and very long tasks.

> **Automatic context management**
> - **At task start**: once the plan passes review, execution continues in a new session.
> - **Long-running tasks** (team tier): context is handed off automatically at about 60%, keeping the AI clear-headed with no interruption to the task.

https://github.com/user-attachments/assets/c85a9a89-2afa-44b0-a4f5-bf6db1db3001

## Platform support

- **Claude Code** — Linux ✓ · Windows (WSL) ✓ · macOS ?
- **DeepSeek Harness** — Linux ✓ · Windows ✓ · macOS ?
- **GitHub Copilot (VS Code)** — Windows ✓ · Linux ? · macOS ?. **Currently solo only.**

`?` means the code is written for compatibility and portability, but has not been actually tested; please open an issue with what you see.

## Install

### Claude Code

Pick one of the following — the skills are plain folders under `platforms/claude-code/skills/`:

- **Option 1: Installer** (recommended):
  ```bash
  git clone https://github.com/AI4XE/Parallax.git
  bash Parallax/install.sh /path/to/your-project
  ```
  Installs all seven skills into `<project>/.claude/skills/`, atomically, and **preserves your local `*.conf` edits on re-install/upgrade**.
- **Option 2: Manual copy**: copy each folder from `platforms/claude-code/skills/` into `<your-project>/.claude/skills/`. Fully functional without the installer; re-copying later overwrites local `*.conf` edits.
- **Option 3: Claude Code plugin marketplace**: `/plugin marketplace add AI4XE/Parallax`, then `/plugin install parallax`. Slash commands get namespaced (`/parallax:qstart`); natural-language triggering ("qstart") still finds them.

Then, inside that project in Claude Code, just say: `qstart` (or `wish` / `Parallax`).

**Permissions**: the team track starts a separate headless executor process. Use the permission mode you already trust for autonomous work; Parallax does not raise permissions on its own and never treats "hand-off" as approval.

> **Tip**: running Claude Code inside `tmux` together with a plugin is the most compatible setup; you can also use tools such as [cc-connect](https://github.com/chenhg5/cc-connect) to connect the session to a chat app for remote follow-up.

### DeepSeek Harness

Pick one of the following:

- **Option 1: Plugin from npm** (preferred — no copying, re-run to upgrade):
  In the DSH web UI: **Plugins → Add plugin → enter `parallax-dsh`** (the plugin list can take ~30 s to load; choose the npm registry), install it, toggle the plugin **on**, then restart the profile. Or from the CLI:
  ```bash
  dsh plugin --profile <profile> add parallax-dsh
  ```
  Requires the Node runtime supplied by dsh and pnpm visible on the `dsh` process PATH (e.g. via corepack); restart the profile after installing. Remove with `dsh plugin --profile <profile> remove parallax-dsh`. **Zero extra setup**: the bundle registers the skills and the native handoff hook and applies `subagent.maxDepth: 3`; do not edit rules, profiles, patches or permission settings for handoff.
- **Option 2: Build the bundle from this repository**:
  ```bash
  git clone https://github.com/AI4XE/Parallax.git
  node Parallax/platforms/deepseek-harness/bundle/build.mjs
  dsh plugin --profile <profile> add "file:<absolute path to Parallax>/platforms/deepseek-harness/bundle"
  ```
  Windows can run both commands natively; quote paths containing spaces. The installed bundle behaves exactly like the npm install (full native handoff, zero extra setup).

Skills-only copying (`install.sh --platform deepseek-harness`) is **not a supported install**: it provides the skill text only, with no native handoff or team support.

Use normal `workspace-write` permissions, not bypass/full access. Keep ordinary shells: confined environments that block named-pipe capture cannot run some subprocess tools.

Then, in a DSH session with your project as the working directory, just say: `qstart` (or `wish` / `Parallax`).

> **Build safety**: `build.mjs` is the cross-platform source of truth; `build.sh` is only a convenience wrapper. Only explicit skill resources are copied, a verified manifest protects generated files, and unknown files, links or changed managed output stop the build without deleting them.

### Copilot

Pick one of the following:

- **Option 1: Installer** (recommended):
  ```bash
  git clone https://github.com/AI4XE/Parallax.git
  bash Parallax/install.sh --platform copilot /path/to/your-project
  ```
- **Option 2: Manual copy**: copy `platforms/copilot/skills/*` into `<your-project>/.github/skills/`, plus `platforms/copilot/agents/*` and `platforms/copilot/instructions/copilot-instructions.md` into the matching `.github/` folders. Do not overwrite an existing `copilot-instructions.md` — merge it.

Then in VS Code, with that folder open:

1. Run **Developer: Reload Window** once (skills and agents are discovered on reload).
2. Trust the workspace once when prompted (remembered per folder).
3. Recommended: set the chat permission mode to **Allow all** (or Autopilot) — setting key `chat.defaultConfiguration` — so cross-session hand-off and subagent reviews run without approval prompts. The default Manual mode also works; you approve each tool type once per session.

Then just say: `qstart` (or `wish` / `Parallax`).

## Skills

Seven skills that turn "please build this" into a supervised, self-reviewing loop:

| Skill | Role |
|---|---|
| **qstart** | Discussion entry: requirements → research → one-question-at-a-time alignment → complete overview. |
| **qgo** | Hand-off: re-checks the discussion, writes the plan, passes the **bcheck** gate (score ≥ 4/5), then a clean-context agent executes autonomously → **rcheck** audit → scoped commit. |
| **bcheck** | Independent adversarial review of the plan *before* execution. The doer never grades their own homework. |
| **gclear** | Saves a distilled brief and hands it to clean model context, with delivery verification; the platform adapter supplies the native mechanism. |
| **rcheck** | Independent reconciliation audit *after* execution: every "done" claim is verified against reality. |
| **xwrap** | Wrap-up: obvious-problem report, todo update, wrapup doc, smart scoped git commit. |
| **wish** | One-line wish entry (许愿创造 / 许愿编程) that forwards to **qstart**. |

One line: **qstart** (align the wish) → **qgo** (release; plan gated by bcheck → clean-context execution → rcheck audit → commit) → **xwrap** (wrap up).

Two execution tracks: **solo** is the default (one agent does it all after the hand-off); say **team** to add an orchestrator watching a separate executor. `ver` / `nover` select versioned vs non-versioned tasks.

How the hand-off works on each harness:

- **Claude Code**: `gclear` drives `/clear` and verifies the injection against the session transcript; the team track runs a headless stream-json executor in its own process, started and watched by the bundled driver. The CLI `tmux` requirement below still applies to `gclear`'s detached launcher.
- **DeepSeek Harness**: the full bundle provides `parallax_handoff`, which replaces model-visible history with a concise handoff at a native safe step. The SID, agent, append-only user transcript, Web tab/composer and child ownership stay intact, and work resumes automatically. In solo you keep talking directly to the executor; in team the original window stays the orchestrator and manages native child executors via `list_agents` / `send_message` / `interrupt_agent`. The bundle sets `subagent.maxDepth: 3`, so an executor can spawn its own independent reviewers.

## Tested versions

- Claude Code **2.1.322**, **2.1.140**
- DeepSeek Harness (dsh) **0.2.2-rc.1**
- VS Code **1.146.0** with bundled GitHub Copilot Chat **0.74.0**

## Philosophy

- **The harness is the key.** Real quality comes from agent collaboration under harness constraints — not from a magic prompt or a bigger model.
- **Three steps, one hand-off.** Wish → choose → hand off. You own the goal and the boundaries; the agents execute, manage context, and independently verify and cross-review, so you steer instead of supervising every step.
- **The loop.** Input → analysis → plan → execution → acceptance → delivery. Every step gets independent review, scoring, and iteration — the one doing the work and the one checking are different entities with isolated contexts.
- **Three pillars**: phase isolation (strategy vs tactics) · adversarial acceptance (anti-sycophancy: the reviewer's job is to break it) · TDD (see it fail before you see it pass).

> **Name**: from Greek *parallaxis* — para- "beside" + allassein "to change". Viewed from beside, the position shifts: change the viewpoint and what you see changes. Self-review from inside cannot; an outside look can.

## Compatibility

- **Git**: recommended — the workflow uses Git roots, scoped commits, and hand-off checkpoints; `qstart` runs one cheap `git --version` during research and asks once whether to install it if missing (it does not block).
- **Claude Code**: Linux · macOS · Windows WSL. Bash 4+, Python ≥ 3.11 (stdlib only; no third-party packages), `tmux` for the CLI hand-off launcher.
- **DeepSeek Harness**: **Runtime uses only dsh's Node and injected native services** — no Python, Bash, zstd CLI, tmux or patched harness.
- **GitHub Copilot**: pure Markdown skills, zero runtime dependencies. Solo only.

## Configuration

Everything works with zero configuration. Optional knobs, each a tiny local file (kept across upgrades by the installer):

- `skills/bcheck/skill.conf`, `skills/rcheck/skill.conf` — `review_model=` : model for the independent review subagent. Empty = session default (never up- or down-shifted).
- Claude Code only: `skills/gclear/gclear.conf` — `MODEL_ALIASES=""` : space-separated `alias=full-id` pairs for `gclear --model`; plus timeout/retry tuning keys.
- Default reply language per skill (trigger-message language and spoken requests override it): Claude Code reads the `display_lang` frontmatter key; DeepSeek Harness reads `metadata.display_lang`.

## Upgrading

Re-run the installer (`bash install.sh [--platform <p>] <project>`) — it swaps skills atomically and keeps your local `*.conf` files. Claude Code plugin installs: `/plugin update parallax`. DeepSeek Harness bundle installs: re-run `node platforms/deepseek-harness/bundle/build.mjs`, then `dsh plugin --profile <profile> add "file:<bundle path>"` and restart that profile. Each skill versions itself in its `SKILL.md` frontmatter.

## Contact

Questions or feedback: open an issue or email [MatClaw@agentmail.to](mailto:MatClaw@agentmail.to).

---

MIT — see [LICENSE](LICENSE).
