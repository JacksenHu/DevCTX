---
name: devctx
description: "让同一个项目在多个 AI 编程工具/IDE（Cursor、Claude Code、Windsurf、Trae/Trae Work CN、GitHub Copilot、Cline、Roo、Continue、Gemini CLI、Codex、Aider、豆包及任意 work 软件）之间无缝切换开发，并解决切换模型/新开会话导致的上下文丢失与重复读代码浪费 token 的问题。每轮对话结束自动往 .ai-dev/handoffs/ 追加一条压缩记录（时间戳 + Agent-会话Id + 用户原话 + Done/Next/Verify/Risks），支持多 Agent 并发协作不互相覆盖。内置 speckit / Spec Kit 规格驱动开发（SDD）联动：用户说'用 spec/sdd/speckit 做 XXX'时自动跑 specify → plan → tasks → implement 完整流程。触发场景：(1) 用户要在多个 IDE/AI 工具间来回开发同一项目、统一项目规范与规则文件（CLAUDE.md/.cursorrules/.windsurfrules/.trae/rules/AGENTS.md 等）、给项目接入 AI 协作上下文或生成 AGENTS.md；(2) 首次为项目建立统一上下文目录 .ai-dev、工具指针与代码索引；(3) 新会话/换工具/换模型时自动恢复现场（resume）；(4) 每轮对话结束或任务闭环或切换前自动写交接（handoff）、按压缩规范记录到 handoffs/、归档、重建索引、同步指针；(5) 对项目上下文做健康体检（doctor）；(6) 用户提到上下文工程、项目记忆/工作记忆外置、跨工具协作规范、省 token、避免重复扫描代码库；(7) 用户说'用 spec/sdd/speckit 做 XXX'、写功能规格、生成实施计划、任务拆解、按规格实现。"
---

# Cross-IDE Dev Context — 跨工具无缝开发协议

## 第一原则：用户零命令、零设置

- **脚本由 AI（你）调用，永远不要让用户自己去敲脚本路径或命令。** 脚本就在本 SKILL.md 同级的 `scripts/` 目录，用你自己的文件/终端工具执行。
- 用户只需要表达意图（"给这个项目接入 / 接着上次做 / 收工"），其余动作链由你自动完成，完成后用一句话告知结果。
- 用户机器没有 Python 时**不得**把"请先安装 Python"甩给用户：走本文「无 Python 降级路径」，用你的文件工具等价落地。
- **主控 / 协作分工**：只有运行本技能的"主控 AI"需要脚本；其他 IDE 里的 AI 不装本技能，它们靠项目内自包含的 `.ai-dev/` 与指针文件即可运转，因此生成的每份文件都必须不依赖本技能存在。

## 第零原则：先受理，再动手（防特性漂移）

**用户说的话是随意的，不能直接当需求。** 同一个功能可能已经做完、做一半、或正在被改写；
用户会中途插话、会在你输出完之后又补要求。此时若照着字面新开一份文档/任务，做出来的东西就会漂移，
甚至根本不是用户要的。因此**动手前必须先做需求受理**：

```text
用户随口一句话 ──▶ 受理裁定（查证据，不猜）──▶ EXTEND / NEW / CONTINUE / FIX / CORRECTION
                          │
                          ├─ handoffs/INDEX.md 的「开放 Next」  有没有人正在做这件事
                          ├─ specs/*/tasks.md 未闭环项          有没有排着没做完的任务
                          ├─ docs/DOCS.md 文档索引              这个需求有没有主文档
                          └─ code-index.md                     代码里是不是已经有了
```

**裁定优先级**：有 `active` 主文档 → `EXTEND`（改文档，禁止新建）；没有文档但有未闭环记录 →
`CONTINUE`（接着做，并补一份文档把需求定下来）；三类证据都没有 → `NEW`（才允许新建）。
裁定结论**必须写进本轮 handoff 记录的 `### Intent` 小节**，否则下一个 Agent 会重新裁定、
重新建一份文档，漂移从此开始。

**文档纪律**：一个需求只对应一份文档，按主题组织，**严禁按对话轮次组织**。
用户插话 / 追加 / 更正 / 否定刚才的做法 → 全部是**修改现有文档**，不是新建。

## 核心模型

切换工具/模型之所以贵，是因为项目认知只存在易失的对话历史里。本技能把它**外置为项目内一套文件**：

- **单一事实源** `.ai-dev/`：L1 入口 START_HERE、L2 长期记忆（project-brief / architecture / conventions / glossary / lessons / code-index）、L3 工作记忆 HANDOFF、`handoffs/` 每轮对话压缩流水与 decisions/。
- **薄指针**：各工具私有规则文件只写十几行"自动去读 .ai-dev/"，脚本统一生成，规则绝不复制多份。
- **固定开销极小**：新会话只读 START_HERE + HANDOFF + handoffs/INDEX.md（约 1000 token）恢复现场，其余按需，禁止全仓扫描。
- **每轮压缩**：每轮对话结束都往 `handoffs/` 追加一条独立记录（时间戳 + Agent-会话Id + 用户原话 + Done/Next/Verify/Risks），
  而不是把所有东西堆进单个 HANDOFF.md——后者会无限膨胀，且并行 Agent 会互相覆盖。

## 触发判断

| 用户情形 | 动作 | 触发方式 |
|---|---|---|
| 项目没有 `.ai-dev/`，或用户要"多 IDE 统一/接入规范/无缝切换" | **init 全链** | 用户意图 |
| **接到任何需求 / 要写任何文档之前** | **intake 需求受理**（见「第零原则」） | **AI 自动，最先做** |
| 新会话、换 IDE/模型、"接着上次做" | **resume** | AI 自动 |
| **每轮对话结束（你输出回复之后）** | **handoff**：往 `handoffs/` 追加一条压缩记录 + 滚动更新 HANDOFF.md | AI 自动 |
| 用户中途插话 / 输出后又提要求 / 否定刚才的做法 | **intake 重裁** → 并进**同一份**需求文档，绝不新建 | AI 自动 |
| 增删/移动源码文件或模块 | **reindex**（必要时同步 architecture） | AI 自动 |
| 难以逆转的选型/架构决定 | **decision**（ADR） | 用户意图 |
| 新装工具、指针被覆盖、用户问"状态/正常吗/检查一下" | **doctor / sync** | 用户意图或 AI 自动 |
| `handoffs/` 记录超过约 200 条 | **archive**（搬最旧的进 `archive/handoffs/`，活跃窗口不动） | AI 自动 |
| 用户说"用 spec/sdd/speckit 做 XXX" | **speckit 全链**（见 §7） | 用户意图 |

## 1. init 全链（每个项目一次，AI 自动完成）

1. 定位项目根（向上找 `.git`，否则用用户给的目录）。脚本路径用本技能目录：`<本SKILL.md所在目录>/scripts/init_project.py <项目根>`（默认 detected；用户明确要全工具覆盖加 `--all`）。
2. 脚本会：建 `.ai-dev/`（模板来自 `assets/templates/`，不覆盖已有文件）、生成首份 code-index、给在用工具写指针。
3. **脚本跑完不是结束**：你必须继续自动完成，不许把空模板留给用户——
   - 以 code-index 为地图扫描真实代码，**亲自起草** project-brief / architecture / conventions / glossary（术语表只留会被猜错的业务黑话与缩写）/ lessons（永久教训，初始可空）：事实只来自代码与用户陈述，不确定写"待确认"，禁止编造版本、命令、目录；
   - **项目现状 spec 化**：扫描代码里的 TODO / FIXME / XXX、raise NotImplementedError、空函数、半成品模块；扫 git log 最近方向。把发现的功能逐个写进 specs/：已完成的写 spec.md（标注状态"已完成"），未完成的完整跑 spec → plan → tasks（见 §7）；不确定的标 [待确认]；
   - 起草后把"仅靠代码定不了、又会改变后续方向"的关键问题（真实运行命令、业务术语含义、本期边界）**一次性集中**向用户确认，不逐条打断、不为问而问；
   - architecture 要写出模块分区与"改什么去哪改"速查；HANDOFF 写入初始状态，元信息"进行中的 spec"列出所有未闭环的 spec；
   - 补项目特有忽略到 `.ai-dev/ignore.conf`（部署运行时目录、构建产物、凭据目录），重建一次索引；
   - 跑一次 `doctor.py --fix` 确认全绿。4. 用一句话向用户交付："已接入，以后直接提需求；换其他 IDE 新开会话即自动接续"，并说明哪些项待他决策（如 git、过期凭据），不罗列过程。

### 无 Python 降级路径（环境无 python/python3 时）

用你的文件工具手工完成与脚本等价的结果，不许要求用户安装任何东西：

1. 把 `assets/templates/` 下 START_HERE、HANDOFF、project-brief、architecture、conventions、glossary、lessons、ignore.conf 复制为 `<项目根>/.ai-dev/` 同名文件（替换 {{DATE}}），ADR 模板放 `decisions/0000-template.md`，建空 `archive/`。
2. 按 `scripts/_common.py` 的 TOOL_SPECS 找到目标工具的规则文件相对路径（检测到在用的工具，外加 AGENTS.md），写入 POINTER_BODY 同等内容（frontmatter 规则照 _common.py）。
3. code-index 手工生成：列目录树 + 每个源码文件"路径（约 N 行）— 头部注释一句话"，忽略规则等价 ignore.conf。
4. 后续 reindex/archive 同样手工增量完成，并在 HANDOFF 注明"本项目由无脚本路径初始化"。

## 2. intake 需求受理（动手前必做，防特性漂移）

用户的话是随意的：功能可能已做完、做一半，或正被改写；用户会插话、会在你输出后补要求。
**照字面理解就会漂移。** 所以每次接到需求、每次要写文档前，先跑受理：

```bash
python scripts/doc_registry.py <项目根> --intake --user-file <用户原话文件>
# 或短消息：--intake --user "把登录改成手机号"
python scripts/doc_registry.py <项目根> --find <主题词>     # 手工复核有没有主文档
```

脚本会扫描四类证据并给出判定，你**必须按判定行动**，不得绕开：

| 判定 | 含义 | 动作 |
|---|---|---|
| `EXTEND` | 已有 `active` 文档覆盖该主题 | **改那份文档**（在「需求变更」追加本次原话），禁止新建 |
| `CONTINUE` | 有未闭环记录/specs，但还没有文档 | 接着做，并**补一份文档**把需求定下来 |
| `NEW` | 三类证据均未覆盖 | 先 `--find` 换关键词二次确认，再建 `docs/<领域>-<主题>.md` 并登记 |
| `FIX` | 修复既有行为 | 定位到规定该行为的那份文档，改它 |
| `CORRECTION` | 用户否定/更正刚才的做法 | 并进**当前那份**需求文档，并在记录里写清"更正自上一条" |

**文档纪律（硬性）**：

- 一个需求只对应一份文档，**按主题命名**（`auth-sms-login.md`），
  **严禁按对话轮次命名**（`登录-第2次.md` 是反模式，doctor 会报重复主题）。
- 用户插话 / 追加 / 更正 / "不对，应该…" → 全部并进现有文档的「需求变更」小节，**绝不新建**。
- 每份文档必须登记进 `docs/DOCS.md`（主题 / 状态 / 来源需求 / 最近更新）；
  未登记就被 doctor 以**错误级**报出。
- 文档只记"这个需求定成什么样、现状如何"，**不记过程**；过程在 `handoffs/`，活跃状态在 `HANDOFF.md`。
- 文档超过两屏 → 拆成同主题子文档并互相引用，仍然一个需求一条线。

**裁定留痕**：本轮 handoff 记录必须写 `### Intent`，例如：

```text
### Intent
EXTEND → docs/auth-sms-login.md（用户中途插话：验证码改用固定测试码，续上一条的 Next 第 1 条）
```

没有这一行，下一个 Agent 无法知道该改哪份文档，只会重新建一份——漂移就是这样发生的。

## 3. resume（新会话自动执行，不要求用户提醒）

1. 读 START_HERE + HANDOFF + `handoffs/INDEX.md`，暂不预读 L2；沿 HANDOFF「下一步」继续。
   `INDEX.md` 的「开放 Next」列是**任何 Agent（含其他人）还欠的活**，动手前先扫，避免撞车。
   若 HANDOFF 记了"进行中的 spec"，按该路径读 `specs/<名>/tasks.md` 对应条目继续 speckit 流程。
2. 需要某一轮的完整上下文时，按索引文件名读 `handoffs/<该条>.md`；
   有本技能时可直接跑 `scripts/log_handoff.py <根> --tail N`（加 `--agent X` 可只看某个 Agent）。
3. 一两句话对齐现状即可，任务清晰直接开工，有歧义/风险才确认。
4. 定位代码先查 code-index，只精读目标文件；文档与代码冲突以代码为准并回写文档。动手前先查 `lessons.md`。
5. 工具不自动加载规则文件时，用 references/tool-integrations.md §4 的开场白兜底。

## 4. handoff（**每轮对话结束自动执行**，省 token 的核心）

**每轮对话结束（你输出回复之后）都要写，不要等大任务完成。** 这是多会话并发、换 agent 不丢上下文的关键。
单个 `HANDOFF.md` 装不下"每轮都写"（无限膨胀）且并行 Agent 会互相覆盖，因此每轮写**独立小文件**进 `.ai-dev/handoffs/`，
由 `INDEX.md` 一行一条汇总——换模型/换会话/换 Agent 后读一个索引即可定位并恢复现场。

### 3.1 每轮压缩规范

每条记录必须包含四部分，其中**后四段可缺省、可任意组合，但至少要有一段**：

```text
1. 时间戳        ISO 8601 带时区，例 2026-10-12T16:20:33+08:00
2. Agent-会话Id  哪个 Agent、哪个会话产生的，例 claude-code / a1b2c3d4
3. 用户原话      用户这一轮的完整原始需求，一字不删、不改写、不概括
4. 本轮压缩      Done / Next / Verify / Risks 任意组合
```

| 段 | 写什么 | 取舍标准 |
|---|---|---|
| `Done` | 已经做了什么，具体到可验证结果（改了哪个文件、跑到什么状态） | 下一个模型不知道它，会不会走错路或重复劳动？会则留 |
| `Next` | 下一步做什么，按依赖排序，用 `- [ ]`，完成即勾选 | 同上 |
| `Verify` | 要跑什么测试 / 命令来证明 Done 真的成立 | 同上 |
| `Risks` | 有什么坑：试过且走不通的路、容易踩的雷、要避开的事项 | 同上 |

删：对话过程、情绪与寒暄、已被代码自解释的实现细节、大段代码/日志/报错原文（写路径即可）。
**用户原话必须完整**——它是换 Agent 后还原"到底要做什么"的唯一依据，禁止概括。

### 3.2 落地方式（有脚本 / 无脚本两条路）

- **有本技能**：调用 `scripts/log_handoff.py <项目根>`。用户原话走 `--user-file`（长消息一字不删），
  压缩正文走 `--body-file`（含 `### Done` 等标题）或直接用 `--done/--next/--verify/--risks` 快捷参数。
  Agent / 会话 Id 用 `--agent` / `--session` 显式指定，**同一会话内复用同一个 session id**，换会话必须换 Id；
  也可设环境变量 `DEVCTX_AGENT` / `DEVCTX_SESSION_ID`。脚本自动追加记录并原子重建 `INDEX.md`，并发写不会互相覆盖。
- **无 Python 降级**：照 `assets/templates/handoff-entry.md`（已复制为 `.ai-dev/handoffs/0000-template.md`）手工写一个文件到
  `.ai-dev/handoffs/`，文件名 `<时间戳>-<Agent>-<会话Id>.md`，再在 `INDEX.md` 补一行。
  时间戳用当前时间；Agent 写你所在工具名；会话 Id 自己生成一个短 id 并在本会话内复用。

### 3.3 每轮顺手：滚动更新 `HANDOFF.md`

把 `handoffs/` 里最新那条的结论并进 `HANDOFF.md` 顶部（当前任务 / 进度 / 下一步 / 陷阱 / 改动清单），
让只读 `HANDOFF.md` 的会话也能恢复现场。`HANDOFF.md` 是当前状态总览，`handoffs/` 是每轮原始流水，不复制内容只做汇总。
结构变动顺手 reindex，重大决策补 ADR。
**HANDOFF 陷阱区某个坑重复出现第二次，就提升到 `lessons.md`**（永久教训，不随压缩丢失）；动手前先查 lessons.md。

### 3.4 多 Agent 协作

- **各写各的文件**：一条记录一个文件，文件名带 Agent 与会话 Id，永不覆盖别人的记录。
- **先读索引再动手**：读 `handoffs/INDEX.md` 的「开放 Next」列看有没有人正在做同一件事；
  要接手别人的活，先读那条记录拿完整上下文，再在自己的记录里写明"接手自 <文件名>"。
- **`HANDOFF.md` 是共享黑板**：唯一被所有人滚动更新的文件，冲突时以代码现状为准，并把冲突写进自己那条记录的 `Risks`。
- 若项目用了 speckit / Spec Kit（存在 `.specify/` 或 `specs/`），handoff 时**自动**：列出 `specs/` 下未闭环的 spec，
  读对应 `tasks.md` 勾选项算进度（已完成 N / 共 M 条），把"specs/<名>/tasks.md 第 N/M 条（正在做什么）"写进 HANDOFF 元信息；
  已闭环的 spec 不写。spec.md / plan.md / tasks.md 归 `specs/`，不要复制进 `.ai-dev/`。

## 5. 维护脚本（AI 调用；均纯标准库、跨平台、UTF-8、幂等）

```text
scripts/init_project.py <根> [--all] [--force]   # 初始化 + 首份索引 + 指针 + handoffs/ + docs/
scripts/doc_registry.py <根>                     # 需求受理 + 文档登记（动手前必跑）
    --intake --user-file U | --user "..."        # 受理裁定：改现有文档还是新建（含证据）
    --find <关键词>                              # 找现有文档
    --add <文件> --topic <主题> --source <来源>  # 登记一份文档
    --list / --rebuild / --check / --archive <文件>
scripts/log_handoff.py <根>                      # 追加一轮压缩记录 + 重建 INDEX（每轮收工调用）
    --user-file U --body-file B [--agent A] [--session S] [--model M] [--intent "裁定结论"]
    --user "..." --done "..." --next "..." --verify "..." --risks "..." --intent "..."   # 快捷参数，可缺省组合
    --template                # 打印条目模板
    --list / --tail N [--agent A]   # 读：全部一览 / 最近 N 条全文（换 Agent 后恢复现场）
    --rebuild-index / --init  # 只重建索引 / 建 handoffs/
scripts/build_index.py <根> [--max-lines 1200]   # 重建代码地图（尊重 .gitignore 与 ignore.conf）
scripts/sync_rules.py <根> [--all] [--inject] [--check]   # 指针同步/校验
scripts/archive_handoff.py <根> [--max-lines 120] [--max-entries 200] [--entries-only]
scripts/doctor.py <根> [--fix]                   # 体检：缺文件/L2空模板/索引过时/HANDOFF超限/指针漂移/git误忽略/handoffs腐化/文档腐化
```

- **doc_registry**：`--intake` 扫四类证据（handoffs 开放 Next、specs 未闭环、docs 索引、code-index）给出
  NEW/EXTEND/CONTINUE/FIX/CORRECTION 判定；裁定优先级固定为 active 文档 > 未闭环记录 > specs，
  **不得跳过**。`DOCS.md` 原子重建；未登记文档以错误级报出。
- **log_handoff**：一条记录一个文件，只增不改；`INDEX.md` 用临时文件 + 原子替换重建，并有文件锁，
  多 Agent 并发写安全。同秒同 Agent 同会话的第二次写入自动加序号防碰撞。
  `--intent` 在涉及"改/建文档"时**必填**（记录裁定结论，防后续 Agent 重新建文档）。
- **sync_rules**：纯指针随时刷新；手写规则文件默认不覆盖只报 conflict，`--inject` 用 begin/end 标记块注入、块外内容绝不丢，可重复执行。
- **archive_handoff**：绝不触碰归档线以上的活跃区；`--max-entries` 只把最旧的对话记录搬进 `archive/handoffs/`，活跃窗口不动；
  活跃区本身超限只提示 AI 手工压缩。
- **doctor**：无参数出分级报告（退出码 0 健康 / 1 警告 / 2 错误）；`--fix` 自动补缺、重建过时索引、归档、同步指针、
  重建 handoffs 与 docs 索引。handoffs 专项检查：索引与磁盘漂移、记录缺 schema/缺用户原话/无压缩节、记录数超限、
  HANDOFF.md 落后于流水。docs 专项检查：文档未登记（错误级）、缺主题/缺来源需求、主题重复、无记录引用。
  用户问"正常吗/检查下"或 init 收尾时运行。
- 新增工具支持：只改 `scripts/_common.py` 的 TOOL_SPECS 一处。

## 6. decision（ADR）

复制 `decisions/0000-template.md` 为 `NNNN-短标题.md`，记背景、备选、决策、后果，只记"为什么"。

## 7. speckit / Spec Kit 联动（用户说"用 spec 做 XXX"时自动执行）

本技能内置 [github/spec-kit](https://github.com/github/spec-kit) 规格驱动开发（SDD）流程。用户只装 devctx，说"用 spec/sdd/speckit 做 XXX"即可自动跑完整流程，不需要单独安装 spec-kit skill。

### 前置检查（AI 自动）

1. 项目已完成 devctx init（`.ai-dev/` 存在）；没有则先跑 §1 init。
2. 检查 specify CLI 是否可用（`specify version`）：
   - **有 CLI**：自动跑 `specify init . --integration copilot`（如无 `.specify/`），用官方模板和脚本。
   - **没 CLI**：不强制安装，AI 直接手写 spec.md / plan.md / tasks.md，功能完全一样。用户想以后装了再补 `.specify/` 也行。

### 自动流程

| 阶段 | AI 做什么 | 产物 |
|---|---|---|
| **spec** | 从用户需求提炼 2-4 词短名，创建 `specs/<NNN-短名>/spec.md`，填用户场景（P1/P2/P3 + Given/When/Then）、功能需求、成功指标 | spec.md |
| **plan** | 读 spec.md，写技术方案、架构决策、实施步骤、验证方式 | plan.md |
| **tasks** | 读 plan.md，拆任务清单：每个任务含依赖、并行标记 [P]、TDD 验收标准 | tasks.md |
| **implement** | 按 tasks.md 依赖顺序执行，每完成一个勾 `[x]`，跑测试验证 | 代码 + 勾选 tasks.md |

### handoff 自动同步

handoff 时自动读 `specs/<进行中名>/tasks.md`，数勾选数量（已完成 N / 共 M 条），写进 HANDOFF 元信息：
> 进行中的 spec：`specs/003-login-refactor/tasks.md` 第 3/7 条（正在做"拖拽排序"）

换工具后新 AI 读 HANDOFF 就知道在 spec 流程哪一步，不用重读整个 spec。

### 注意

- spec.md / plan.md / tasks.md 归 `specs/`，不要复制进 `.ai-dev/`。
- 项目原则（constitution）在 `.specify/memory/constitution.md`，每阶段开始前读并遵守。
- spec-kit 是 MIT 许可证，与本项目无冲突。

## Token 经济硬规则

1. 新会话固定只读 START_HERE + HANDOFF；L2 与源码按需、按索引读。
2. 同一规则只存一处，复用写路径引用，不复制。
3. HANDOFF 限长 120 行，超限先归档；文档增量更新，不整体重写。
4. code-index 能回答的问题，不遍历目录、不批量读源码。
5. 忽略名单维护在 ignore.conf，索引不制造噪音；凭据/密钥目录必须忽略且内容不外传。

## 资源索引

- `assets/templates/` — 复制进项目的全部上下文模板（自包含，不依赖本技能）；
  其中 `handoff-entry.md` 是每轮对话压缩记录的格式模板，会复制为 `.ai-dev/handoffs/0000-template.md`
- `references/context-protocol.md` — 分层模型、token 预算、每轮压缩规范、SOP 细节、无脚本降级与反模式；定制协议或用户追问原理时读
- `references/tool-integrations.md` — 各工具规则位置/加载机制（含 Trae Work CN 专项）、兜底挂载、团队 git/CI；具体工具适配时读

## 完成前自检

- `.ai-dev/` 必备文件齐全，L2 是据实填写的实质内容而非空模板（glossary / lessons 可选，存在则应已填写）；doctor 无 error。
- `handoffs/` 已初始化，本轮对话已追加一条符合压缩规范的记录（时间戳 + Agent-会话Id + 完整用户原话 + 至少一段 Done/Next/Verify/Risks），
  `INDEX.md` 与磁盘记录数一致，`HANDOFF.md` 已滚动进最新结论。
- **动手前跑过 `--intake` 并按其判定行动**；涉及改/建文档时，本轮记录写了 `### Intent` 指明落到哪份文档。
- `docs/` 里本需求的文档已登记进 `DOCS.md`（主题 / 状态 / 来源需求），
  **没有按对话轮次新建的第二份文档**；用户插话与追加都已并进同一份文档。
- code-index 与当前文件结构一致、无运行时噪音；在用工具指针同步、手写规则未被破坏。
- HANDOFF 能让全新会话仅凭它 + START_HERE 对齐现状，且不超长。
- 全程未让用户手动执行命令；无 Python 时已走降级路径落地。
