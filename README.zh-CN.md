![Parallax](assets/banner.zh-CN.svg)

Parallax 是 AI 编程助手之上的一个轻量插件，让你体验更省心的「许愿创造」模式。

随口说想做什么，做几个选择题，然后放手——剩下的交给智能体团队。你专注目标，AI负责干活、独立复查和上下文管理。

[English](README.md)

[![Claude Code](https://img.shields.io/badge/Claude_Code-Install-6B4FBB?logo=anthropic&logoColor=white)](#claude-code)
[![DeepSeek Harness](https://img.shields.io/badge/DeepSeek_Harness-Install-4D6BFE?logo=deepseek&logoColor=white)](#deepseek-harness)
[![VS Code Copilot](https://img.shields.io/badge/VS_Code_Copilot-Install-0098FF?logo=githubcopilot&logoColor=white)](https://marketplace.visualstudio.com/items?itemName=ai4e.parallax-github-copilot)
[![Listed on dsh-plugin.org](https://dsh-plugin.org/badges/listed.svg)](https://dsh-plugin.org/plugins/ai4xe/parallax)
[![License](https://img.shields.io/github/license/AI4XE/Parallax)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/AI4XE/Parallax)](https://github.com/AI4XE/Parallax)

## 三步

> **插件触发词**：`wish` · `Parallax` · `qstart` · `许愿创造` · `许愿编程`

1. **许愿** —— 随口说你想做什么，不用想清楚。可以分多次说，说完发一句「没有了」。
2. **做选择题** —— 智能体先调研，再和你一次一问地对齐；你只拍板，也可随意补充需求或探讨。
3. **放手** —— 其余交给智能体团队：方案 → 独立审查 → 新会话执行 → 独立对账 → scoped 提交 → 收尾。

> **两种模式**
> - **solo**（默认）：你直接面对执行会话，当前会话就是执行者，适合日常使用。
> - **team**：你的对话始终与编排者进行，执行者在独立会话中运行，适合大型和超长任务。
>
> **上下文自动管理**
> - **任务开始**：计划通过审查后，自动新会话执行。
> - **长程任务**（team 档）：默认在上下文约 60% 时自动交接，保持 AI 头脑清醒，任务不中断。

https://github.com/user-attachments/assets/6dd03b96-7a3a-44d4-85a4-78c70508a4c0

## 支持平台

- **Claude Code** —— Linux ✓ · Windows（WSL）✓ · macOS ?
- **DeepSeek Harness** —— Linux ✓ · Windows ✓ · macOS ?
- **GitHub Copilot (VS Code)** —— Windows ✓ · Linux ? · macOS ?。**目前暂时只支持 solo 档。**

`?` 表示代码按兼容性/移植性编写，但未实际测试；发现问题欢迎提 issue。

## 安装

### Claude Code

- **方式一：安装脚本**（推荐）：
  ```bash
  git clone https://github.com/AI4XE/Parallax.git
  bash Parallax/install.sh /path/to/your-project
  ```
- **方式二：手动拷贝**：把 `platforms/claude-code/skills/` 下各文件夹拷入 `<your-project>/.claude/skills/`。
- **方式三：Claude Code 插件市场**：`/plugin marketplace add AI4XE/Parallax`，然后 `/plugin install parallax`。斜杠命令带命名空间（`/parallax:qstart`）；自然语言触发（如「qstart」）照样可用。

**说明**：team 档会启动额外的独立的 headless 执行者进程，编排者负责管理和回收。

**建议**：在 `tmux` 中运行 Claude Code 并配合插件使用最兼容；也可用 [cc-connect](https://github.com/chenhg5/cc-connect) 等工具把会话接入聊天软件，方便远程跟进。

### DeepSeek Harness

- **方式一：从 npm 安装插件**（首选——零拷贝，升级重跑即可）：
  DSH 网页 →「添加插件」→ 输入 `parallax-dsh`（插件列表首次加载可能需要约 30 秒；选择 npm 源），安装后把插件开关**打开**，再重启 profile；或用 CLI：
  ```bash
  dsh plugin --profile <profile> add parallax-dsh
  ```
  要求 dsh 自带的 Node，且 `dsh` 进程 PATH 上能找到 pnpm（如经 corepack 提供）；安装后重启 profile。卸载：`dsh plugin --profile <profile> remove parallax-dsh`。**装完零额外设置**：bundle 注册技能与原生交接 hook，自带 `subagent.maxDepth: 3`；交接不要求编辑规则、profile、patch 或权限配置。
- **方式二：从本地仓库构建 bundle**：
  ```bash
  git clone https://github.com/AI4XE/Parallax.git
  node Parallax/platforms/deepseek-harness/bundle/build.mjs
  dsh plugin --profile <profile> add "file:<Parallax 绝对路径>/platforms/deepseek-harness/bundle"
  ```
  Windows 可原生执行上述两条命令，含空格路径须加引号。该方式与 npm 安装等价（完整原生交接、零额外设置）。

纯技能拷贝（`install.sh --platform deepseek-harness`）**不是受支持的安装方式**：它只提供技能文本，没有原生交接与 team 支持。

**说明**：Parallax 在 DeepSeek Harness 的 `workspace-write` 权限下也可运行。

然后在以你的项目为工作目录的 DSH 会话里，只需说：`qstart`（或 `wish` / `Parallax`）。

### Copilot

- **方式一：市场安装**（推荐）：在 VS Code 扩展市场搜索 **Parallax for Copilot**（发布者 `ai4e`）并安装。
- **方式二：项目内安装**（安装脚本）：
  ```bash
  git clone https://github.com/AI4XE/Parallax.git
  bash Parallax/install.sh --platform copilot /path/to/your-project
  ```
- **方式三：手动拷贝**：把 `platforms/copilot/{skills,agents,instructions}` 拷入 `.github/`。已有 `copilot-instructions.md` 时不要覆盖，请合并。

**说明**：

1. 第一次运行时建议执行一次 **Developer: Reload Window**（技能与 agent 在 reload 时被发现）。
2. 建议：把聊天权限档设为 **Allow all**（或 Autopilot）——设置键 `chat.defaultConfiguration`——跨会话交接与子代理审查即零确认全自动。默认 Manual 档也能用，只是每种工具每会话首次要批一次。

## 技能

Parallax 把「帮我做个东西」变成一条自动推进和自我审查的循环流水线：

| 技能 | 角色 |
|---|---|
| **qstart** | 讨论入口：需求 → 调研 → 一次一问对齐 → 完整总览。 |
| **qgo** | 放手执行：复核讨论、写方案、过 **bcheck** 门禁（评分 ≥ 4/5），然后干净上下文的 agent 自主执行 → **rcheck** 对账 → scoped 提交。 |
| **bcheck** | 执行前的独立对抗审查。干活的人不给自己批作业。 |
| **gclear** | 落盘精炼交接内容，交给干净模型上下文并验证投递；具体机制由各平台适配。 |
| **rcheck** | 执行后的独立对账审查：每条「已完成」都要对着实际结果核一遍。 |
| **xwrap** | 收尾：明显问题报告、todo 更新、wrapup 文档、智能 scoped git 提交。 |
| **wish** | 一句话许愿入口，转发 **qstart**。 |

一行话：**qstart**（对齐愿望）→ **qgo**（放手；方案过 bcheck 门 → 干净上下文执行 → rcheck 对账 → 提交）→ **xwrap**（收尾）。

两种协作模式：规划与执行分属不同会话、自动衔接；**solo** 模式不设编排，执行会话一干到底；**team** 模式的执行阶段由编排者与独立执行者协作完成。`ver` / `nover` 选择版本任务与非版本任务。

各 harness 上的交接方式：

- **Claude Code**：`gclear` 驱动 `/clear` 并对着会话 transcript 验证注入；team 档在独立进程中运行 headless stream-json 执行者，由套件自带的驱动拉起与守护。下文的 CLI `tmux` 依赖仍然适用于 `gclear` 的分离式启动器。
- **DeepSeek Harness**：完整 bundle 提供 `parallax_handoff`，在原生安全点用精炼交接替换模型可见历史。SID、agent、用户可见的追加式历史、Web tab/composer 与子代理归属不变，并自动续作。solo 原窗直接面对执行者；team 原窗仍是编排者，通过 `list_agents` / `send_message` / `interrupt_agent` 管理原生执行者子代理。bundle 设置 `subagent.maxDepth: 3`，执行者可自派独立审查者。

## 实测版本

- Claude Code **2.1.322**、**2.1.140**
- DeepSeek Harness（dsh）**0.2.2-rc.1**
- VS Code **1.146.0**，内置 GitHub Copilot Chat **0.74.0**

## 理念

- **Harness 是关键的一环。** 真正的质量来自 harness 约束下的智能体协作——不靠魔法提示词。
- **三步、一次放手。** 许愿 → 做选择题 → 放手。你负责目标与边界；智能体执行、管理上下文并独立验收、交叉审查，你掌舵，不必盯每一步。
- **循环。** 输入 → 分析 → 方案 → 执行 → 验收 → 交付。每一步都有独立审查、评分与迭代——干活的和检查的永远不是同一个实体、上下文相互隔离。
- **三大支柱**：阶段隔离（战略/战术分层）· 对抗式验收（反谄媚：审查员的职责就是拆台）· TDD（先看到红，再看到绿）。

> **名字**：源自希腊语 *parallaxis* — para-「旁边」+ allassein「改变」。从旁边看，位置就变了：换一个视角，才能看见内部自查看不见的东西。

## 兼容性

- **Git**：建议安装——流程使用 Git 根、scoped 提交与交接检查点；`qstart` 调研时会执行一次 `git --version`，缺失则询问是否安装（不阻塞）。
- **Claude Code**：Linux · macOS · Windows WSL。Bash 4+、Python ≥ 3.11（纯标准库，无第三方依赖），CLI 交接启动器需要 `tmux`。
- **DeepSeek Harness**：**运行时只使用 dsh 自带 Node 与注入的原生服务**——不需要 Python、Bash、zstd CLI、tmux，也不修改 harness。
- **GitHub Copilot**：纯 Markdown 技能，零运行时依赖。仅 solo 档。

## 配置

零配置即可工作。可选开关，各是一个小的本地文件（升级时安装脚本会保留）：

- `skills/bcheck/skill.conf`、`skills/rcheck/skill.conf` —— `review_model=`：独立审查子代理所用模型。留空 = 会话默认模型（不升档、不降档）。
- 仅 Claude Code：`skills/gclear/gclear.conf` —— `MODEL_ALIASES=""`：空格分隔的 `别名=完整模型ID` 对，供 `gclear --model` 使用；另有超时/重试调优项。
- 每个技能的默认回复语言（触发消息语言与口头指定优先）：Claude Code 读 frontmatter `display_lang` 键；DeepSeek Harness 读 `metadata.display_lang`。

## 升级

重跑安装脚本（`bash install.sh [--platform <p>] <项目>`）——原子交换，保留本地 `*.conf`。Claude Code 插件安装：`/plugin update parallax`。DeepSeek Harness bundle：重跑 `node platforms/deepseek-harness/bundle/build.mjs`，再次 `dsh plugin --profile <profile> add "file:<bundle 路径>"`，再重启该 profile。每个技能版本见其 `SKILL.md` frontmatter。

## 联系

问题或反馈：欢迎提 issue，或邮件 [MatClaw@agentmail.to](mailto:MatClaw@agentmail.to)。

---

MIT — 详见 [LICENSE](LICENSE)。
