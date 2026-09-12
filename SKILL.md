---
name: cross-ide-dev-context
description: "让同一个项目在多个 AI 编程工具/IDE（Cursor、Claude Code、Windsurf、Trae/Trae Work CN、GitHub Copilot、Cline、Roo、Continue、Gemini CLI、Codex、Aider、豆包及任意 work 软件）之间无缝切换开发，并解决切换模型/新开会话导致的上下文丢失与重复读代码浪费 token 的问题。触发场景：(1) 用户要在多个 IDE/AI 工具间来回开发同一项目、统一项目规范与规则文件（CLAUDE.md/.cursorrules/.windsurfrules/.trae/rules/AGENTS.md 等）、给项目接入 AI 协作上下文或生成 AGENTS.md；(2) 首次为项目建立统一上下文目录 .ai-dev、工具指针与代码索引；(3) 新会话/换工具/换模型时自动恢复现场（resume）；(4) 任务闭环或切换前自动写交接（handoff）、归档、重建索引、同步指针；(5) 对项目上下文做健康体检（doctor）；(6) 用户提到上下文工程、项目记忆/工作记忆外置、跨工具协作规范、省 token、避免重复扫描代码库。"
---

# Cross-IDE Dev Context — 跨工具无缝开发协议

## 第一原则：用户零命令、零设置

- **脚本由 AI（你）调用，永远不要让用户自己去敲脚本路径或命令。** 脚本就在本 SKILL.md 同级的 `scripts/` 目录，用你自己的文件/终端工具执行。
- 用户只需要表达意图（“给这个项目接入 / 接着上次做 / 收工”），其余动作链由你自动完成，完成后用一句话告知结果。
- 用户机器没有 Python 时**不得**把“请先安装 Python”甩给用户：走本文「无 Python 降级路径」，用你的文件工具等价落地。
- **主控 / 协作分工**：只有运行本技能的“主控 AI”需要脚本；其他 IDE 里的 AI 不装本技能，它们靠项目内自包含的 `.ai-dev/` 与指针文件即可运转，因此生成的每份文件都必须不依赖本技能存在。

## 核心模型

切换工具/模型之所以贵，是因为项目认知只存在易失的对话历史里。本技能把它**外置为项目内一套文件**：

- **单一事实源** `.ai-dev/`：L1 入口 START_HERE、L2 长期记忆（project-brief / architecture / conventions / glossary / lessons / code-index）、L3 工作记忆 HANDOFF 与 decisions/。
- **薄指针**：各工具私有规则文件只写十几行“自动去读 .ai-dev/”，脚本统一生成，规则绝不复制多份。
- **固定开销极小**：新会话只读 START_HERE + HANDOFF（约 1000 token）恢复现场，其余按需，禁止全仓扫描。

## 触发判断

| 用户情形 | 动作 | 触发方式 |
|---|---|---|
| 项目没有 `.ai-dev/`，或用户要“多 IDE 统一/接入规范/无缝切换” | **init 全链** | 用户意图 |
| 新会话、换 IDE/模型、“接着上次做” | **resume** | AI 自动 |
| 任务闭环、要切工具/模型、用户说“收工” | **handoff** | AI 自动 |
| 增删/移动源码文件或模块 | **reindex**（必要时同步 architecture） | AI 自动 |
| 难以逆转的选型/架构决定 | **decision**（ADR） | 用户意图 |
| 新装工具、指针被覆盖、用户问“状态/正常吗/检查一下” | **doctor / sync** | 用户意图或 AI 自动 |
| HANDOFF 超约 120 行 | **archive** | AI 自动 |

## 1. init 全链（每个项目一次，AI 自动完成）

1. 定位项目根（向上找 `.git`，否则用用户给的目录）。脚本路径用本技能目录：`<本SKILL.md所在目录>/scripts/init_project.py <项目根>`（默认 detected；用户明确要全工具覆盖加 `--all`）。
2. 脚本会：建 `.ai-dev/`（模板来自 `assets/templates/`，不覆盖已有文件）、生成首份 code-index、给在用工具写指针。
3. **脚本跑完不是结束**：你必须继续自动完成，不许把空模板留给用户——
   - 以 code-index 为地图扫描真实代码，**亲自起草** project-brief / architecture / conventions / glossary（术语表只留会被猜错的业务黑话与缩写）/ lessons（永久教训，初始可空）：事实只来自代码与用户陈述，不确定写“待确认”，禁止编造版本、命令、目录；
   - 起草后把“仅靠代码定不了、又会改变后续方向”的关键问题（真实运行命令、业务术语含义、本期边界）**一次性集中**向用户确认，不逐条打断、不为问而问；
   - architecture 要写出模块分区与“改什么去哪改”速查；HANDOFF 写入初始状态；
   - 补项目特有忽略到 `.ai-dev/ignore.conf`（部署运行时目录、构建产物、凭据目录），重建一次索引；
   - 跑一次 `doctor.py --fix` 确认全绿。
4. 用一句话向用户交付：“已接入，以后直接提需求；换其他 IDE 新开会话即自动接续”，并说明哪些项待他决策（如 git、过期凭据），不罗列过程。

### 无 Python 降级路径（环境无 python/python3 时）

用你的文件工具手工完成与脚本等价的结果，不许要求用户安装任何东西：

1. 把 `assets/templates/` 下 START_HERE、HANDOFF、project-brief、architecture、conventions、glossary、lessons、ignore.conf 复制为 `<项目根>/.ai-dev/` 同名文件（替换 {{DATE}}），ADR 模板放 `decisions/0000-template.md`，建空 `archive/`。
2. 按 `scripts/_common.py` 的 TOOL_SPECS 找到目标工具的规则文件相对路径（检测到在用的工具，外加 AGENTS.md），写入 POINTER_BODY 同等内容（frontmatter 规则照 _common.py）。
3. code-index 手工生成：列目录树 + 每个源码文件“路径（约 N 行）— 头部注释一句话”，忽略规则等价 ignore.conf。
4. 后续 reindex/archive 同样手工增量完成，并在 HANDOFF 注明“本项目由无脚本路径初始化”。

## 2. resume（新会话自动执行，不要求用户提醒）

1. 读 START_HERE + HANDOFF，暂不预读 L2；沿 HANDOFF「下一步」继续。
2. 一两句话对齐现状即可，任务清晰直接开工，有歧义/风险才确认。
3. 定位代码先查 code-index，只精读目标文件；文档与代码冲突以代码为准并回写文档。
4. 工具不自动加载规则文件时，用 references/tool-integrations.md §4 的开场白兜底。

## 3. handoff（任务闭环自动执行，省 token 的核心）

按 HANDOFF 模板更新，取舍标准：**“下一个模型不知道它，会不会走错路或重复劳动？”**
留：目标、可验证完成项、有序下一步、新约束/决策、失败路径、改动文件及原因；
删：对话过程、大段代码/日志、代码自解释的细节。结构变动顺手 reindex，重大决策补 ADR。
**HANDOFF 陷阱区里某个坑重复出现第二次，就提升到 `lessons.md`**（永久教训，不随 handoff 压缩丢失）；动手前先查 lessons.md，避免重蹈覆辙。

## 4. 维护脚本（AI 调用；均纯标准库、跨平台、UTF-8、幂等）

```text
scripts/init_project.py <根> [--all] [--force]   # 初始化 + 首份索引 + 指针
scripts/build_index.py <根> [--max-lines 1200]   # 重建代码地图（尊重 .gitignore 与 ignore.conf）
scripts/sync_rules.py <根> [--all] [--inject] [--check]   # 指针同步/校验
scripts/archive_handoff.py <根> [--max-lines 120]         # 只搬归档线以下历史
scripts/doctor.py <根> [--fix]                   # 体检：缺文件/L2空模板/索引过时/HANDOFF超限/指针漂移/git误忽略
```

- **sync_rules**：纯指针随时刷新；手写规则文件默认不覆盖只报 conflict，`--inject` 用 begin/end 标记块注入、块外内容绝不丢，可重复执行。
- **archive_handoff**：绝不触碰归档线以上的活跃区；活跃区本身超限只提示 AI 手工压缩。
- **doctor**：无参数出分级报告（退出码 0 健康 / 1 警告 / 2 错误）；`--fix` 自动补缺、重建过时索引、归档、同步指针。用户问“正常吗/检查下”或 init 收尾时运行。
- 新增工具支持：只改 `scripts/_common.py` 的 TOOL_SPECS 一处。

## 5. decision（ADR）

复制 `decisions/0000-template.md` 为 `NNNN-短标题.md`，记背景、备选、决策、后果，只记“为什么”。

## Token 经济硬规则

1. 新会话固定只读 START_HERE + HANDOFF；L2 与源码按需、按索引读。
2. 同一规则只存一处，复用写路径引用，不复制。
3. HANDOFF 限长 120 行，超限先归档；文档增量更新，不整体重写。
4. code-index 能回答的问题，不遍历目录、不批量读源码。
5. 忽略名单维护在 ignore.conf，索引不制造噪音；凭据/密钥目录必须忽略且内容不外传。

## 资源索引

- `assets/templates/` — 复制进项目的全部上下文模板（自包含，不依赖本技能）
- `references/context-protocol.md` — 分层模型、token 预算、SOP 细节、无脚本降级与反模式；定制协议或用户追问原理时读
- `references/tool-integrations.md` — 各工具规则位置/加载机制（含 Trae Work CN 专项）、兜底挂载、团队 git/CI；具体工具适配时读

## 完成前自检

- `.ai-dev/` 必备文件齐全，L2 是据实填写的实质内容而非空模板（glossary / lessons 可选，存在则应已填写）；doctor 无 error。
- code-index 与当前文件结构一致、无运行时噪音；在用工具指针同步、手写规则未被破坏。
- HANDOFF 能让全新会话仅凭它 + START_HERE 对齐现状，且不超长。
- 全程未让用户手动执行命令；无 Python 时已走降级路径落地。
