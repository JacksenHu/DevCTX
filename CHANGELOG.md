# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- **存量多 Agent 项目接入（`scripts/retrofit.py`）**：治"项目已被多个 Agent/会话开发过"的接入难题
  - `scan`：按"作者 × 时间窗"（间隔超 14 天自动分窗）把 git 历史切成**历史会话画像**
    （`legacy-bob` / `legacy-alice-2`…），并列存量 .md 清单与 TODO/FIXME 半成品信号；
    无 git 历史时提示口述兜底
  - `plan`：把证据填进接入 SOP，输出该项目的具体顺序（init → spec 化 → 会话回填 →
    文档归并 → 指针 → 体检），并提醒半成品信号是第一批「开放 Next」
  - `baseline --session S --topic T`：为每个历史会话写一条**基线重建记录**
    （agent 固定 `retrofit`）——"压缩各会话 Id"：INDEX 从此列出每个历史会话的开放 Next；
    原话记不得必须写"历史口述"，禁止编造
  - `consolidate [--apply]`：存量文档归并建议（**匹配看正文**，标题常常不含主题词）；
    AI 合并后 `--apply` 把 docs/ 里未登记文档补字段登记（source=RETROFIT）
- **文档治理与需求受理（`.ai-dev/docs/` + `scripts/doc_registry.py`）**：治“文档泛滥与特性漂移”
  - **一个需求只对应一份文档**：需求文档只放 `.ai-dev/docs/`，`DOCS.md` 是唯一登记索引；
    新建前必须先查索引，能并进现有文档的禁止新建；按主题命名，严禁按对话轮次命名（反模式）
  - **intake 需求受理**：动手前跑 `--intake`，拿四类证据（handoffs 未闭环 `Next`、specs 未完成任务、
    DOCS.md 现有文档、代码地图）裁定 `NEW` / `EXTEND` / `FIX` / `CONTINUE` / `CORRECTION`；
    优先级 active 文档 > 未闭环记录 > specs > 全新主题——有主文档就必须落到文档上，
    否则“改需求”会被当成“新任务”重新建文档，即特性漂移的起点
  - **裁定留痕**：handoff 记录新增 `### Intent` 小节写受理结论（例 `EXTEND → docs/auth-sms-login.md`），
    `parse_entry` 支持回读；光有 Intent 没有 Done/Next/Verify/Risks 的记录判不合规
  - `doc_registry.py` 全套命令：`--intake` / `--find` / `--add` / `--archive` / `--list` /
    `--rebuild` / `--check` / `--init`；`--check` 检出未登记文档（错误级）、缺主题/缺来源、
    主题重复、无记录引用
  - 新增 `assets/templates/doc-template.md`（需求文档模板，含需求原文/需求变更/当前结论/已废弃/待办）
    与 `assets/templates/DOCS.md`（登记索引模板）；`init_project.py` 初始化时一并铺好
- `doctor.py` 新增 docs 专项腐化检查：未登记文档（`--fix` 可自愈）、缺主题或缺来源需求、
  主题重复、无 handoff 记录引用
- 新增 `tests/` 覆盖文档登记、intake 六场景裁定（新功能/插话/追加/stable 改需求/续做/无关需求）、
  Intent 回读、索引关联记录数回归
- **每轮对话压缩规范（`.ai-dev/handoffs/`）**：治“上下文一长就失忆”——压缩从此落到文件且有格式
  - 每条记录固定四部分：时间戳（ISO 8601 带时区）+ Agent-会话Id + 用户原话（完整，一字不删）
    + 本轮压缩 `Done` / `Next` / `Verify` / `Risks`（四者可缺省、任意组合，至少一段）
  - 一条记录一个文件 `<时间戳>-<Agent>-<会话Id>.md`，**只增不改**；`INDEX.md` 一行一条自动汇总，
    含 Agent / 会话 / **开放 Next 计数**，换模型/换会话/换 Agent 读一个索引即可定位
  - 新增 `scripts/log_handoff.py`：追加记录、原子重建 INDEX（临时文件 + os.replace + 文件锁）、
    `--tail N` / `--list` / `--agent` 读取、`--template` / `--init` / `--rebuild-index`
  - 多 Agent 并发安全：记录各自独立成文件，写方永不覆盖他人；同秒同身份第二次写入自动加序号
  - 多 Agent 协作约定写进 START_HERE §4：各写各的文件、先读索引的「开放 Next」再动手、
    接手需注明来源、`HANDOFF.md` 是唯一共享黑板
- `doctor.py` 新增 handoffs 专项腐化检查：索引与磁盘漂移、记录缺 `devctx-handoff` 标记 /
  缺用户原话 / 无压缩节（定位到文件名）、记录数超 200 条、HANDOFF.md 落后于流水
- `archive_handoff.py` 支持 `--max-entries N` / `--entries-only`：把最旧的对话记录搬进
  `archive/handoffs/`，活跃窗口绝不触碰
- `init_project.py` 初始化时创建 `handoffs/`、`handoffs/archive/`、复制条目模板为
  `handoffs/0000-template.md` 并生成空 `INDEX.md`
- 新增 `tests/`：覆盖压缩规范解析、多 Agent 并发写、INDEX 一致性、归档、ignore 通配

### Fixed
- **`doctor.py` 语法错误**（`scripts/doctor.py:69` 多余右括号）导致整个脚本无法运行——
  而 init 收尾、`--fix` 自愈、CI `--check` 全都依赖它
- `doc_registry.cmd_add` 的字段写入只替换已存在的行：AI 手工放的文档没有字段表时，
  登记静默变成半登记（状态/来源空缺，doctor 随后报"缺来源需求"）；改为行缺插行、表缺补表
- `doc_registry.rebuild_index` 内部二次 `scan_docs()` 丢弃调用方预设的 `links`，
  导致 `DOCS.md`「关联记录数」永远显示 0；改为在 `rebuild_index` 内统一计算
- `retrofit` 首条提交即 `windows[-1]` 越界；`consolidate` 的"未登记"误用磁盘扫描对比磁盘、
  永远为空，改为对比 `DOCS.md` 索引（与 doctor 的检查一致）；归并匹配补上正文
- `parse_entry` 的 `_SECTION_RE` 不含 `Intent`，受理结论写进记录后读不回来，闭环断裂；
  补入正则，同时把“无压缩节”判定收紧为 Done/Next/Verify/Risks 四节至少一节
- `match_ignore` 未实现 `ignore.conf` 文档承诺的 `**` 任意层级通配与 `/` 根锚定，
  导致 `**/foo`、`/build` 等规则静默失效；改为 glob→正则转换，并保持“后者覆盖前者”的取反语义
- `init_project.py` 命令检测从不产出 `install` / `run` 键，模板里这两项永远显示“待确认”

### Changed
- `SKILL.md` / `README.md` / `references/context-protocol.md` 全面写入压缩规范、多 Agent 协作协议、
  文档治理与需求受理、存量项目接入（retrofit）；handoff 触发条件从"任务闭环/收工"改为**每轮对话结束**，
  intake 成为**动手前的第零原则**
- `START_HERE.md` schema 升级到 `v2`：文件地图登记 `handoffs/` 与 `docs/`，新增 §1.5 需求受理、
  §3 收工协议（压缩规范）、§4 多 Agent 协作约定（含"一个需求一份文档"）；
  `HANDOFF.md` 元信息新增"最近一轮对话记录"字段
- `_common.py` 的 `POINTER_BODY` 增加三句：新会话读 `handoffs/INDEX.md`、每轮结束追加压缩记录、
  动手前先查 `docs/DOCS.md` 做需求受理
- `HANDOFF.md` 与 `handoffs/` 分工明确：前者是当前状态总览（滚动更新），后者是每轮原始流水（只增不改）；
  `docs/` 只记"需求定成什么样"，过程归流水、状态归 HANDOFF

## [0.4.1] - 2026-09-30

### Changed
- **handoff 频率改为每轮对话结束自动执行**（原"任务闭环"）：每轮对话的工作都压缩进 HANDOFF，下一轮（无论哪个 agent、哪个工具、哪个会话）读 START_HERE + HANDOFF 就能无缝接续。这是多会话并发、换 agent 不丢上下文的关键。
- README 图解改回 SVG（矢量清晰，PNG 太模糊）。
- README 日常工作流对齐所有功能：init 自动 spec 化项目现状、自动检测技术栈（Node/Python/Go/Rust + monorepo + .env）、doctor 七类腐化检查。

## [0.4.0] - 2026-09-14

### Added
- **内置 Spec Kit SDD 工作流**：用户只装 devctx，说"用 spec 做 XXX"自动跑 spec → plan → tasks → implement 完整流程
  - 不需要单独装 spec-kit skill
  - 不需要 specify CLI（AI 直接写 markdown 文件，CLI 是可选增强）
  - handoff 自动读 tasks.md 勾选状态算进度
- **init 自动 spec 化项目现状**：
  - 扫描 TODO / FIXME / XXX、raise NotImplementedError、空函数、半成品模块
  - 扫 git log 最近方向
  - 已完成的功能写 spec.md（标"已完成"）
  - 未完成的功能完整跑 spec → plan → tasks
  - 不确定的标 [待确认]

### Changed
- README speckit 联动节重写：从"两个 skill 各自安装"改为"devctx 内置，用户只装一个"
- SKILL.md frontmatter description 加 spec 触发词

## [0.3.0] - 2026-09-13

### Added
- `init_project.py` 自动检测技术栈：
  - 检测 package.json / requirements.txt / pyproject.toml / go.mod / Cargo.toml，自动填语言和框架
  - 自动识别 npm scripts、pytest、ruff、go build/test、cargo build/test 等常用命令
  - 自动检测 Dockerfile / docker-compose / .github/workflows 部署方式
  - **monorepo 支持**：扫描 `packages/*` / `apps/*` / `services/*` / `modules/*`，自动记录子项目技术栈
  - **.env.example 检测**：自动读取环境变量名，预填到 project-brief
- `HANDOFF.md` 下一步动作改为 checkbox 列表：完成即勾选，不用每次重写整段
- `START_HERE.md` 顶部加 `<!-- devctx-schema: v1 -->` 版本标记
- `doctor.py` 新增 schema 版本检查：老项目没有版本标记时警告
- `conventions.md` 模板加具体 Conventional Commits 示例

### Changed
- speckit 联动文档修正为推荐官方 [github/spec-kit](https://github.com/github/spec-kit)，dceoy 包降级为可选
- 项目改名 cross-ide-dev-context → devctx

## [0.2.0] - 2026-09-13

### Added
- 与 [github/spec-kit](https://github.com/github/spec-kit) 文件系统对接：
  - HANDOFF 模板新增"进行中的 spec"字段
  - handoff 时自动读 specs/*/tasks.md 勾选状态算进度
  - README speckit 联动节重写

### Changed
- 项目改名 cross-ide-dev-context → devctx（6 字符）
- marker 从 cross-ide-dev-context:begin → devctx:begin

## [0.1.3] - 2026-09-12

### Added
- 与 [speckit-agent-skills](https://github.com/dceoy/speckit-agent-skills)（上游 [github/spec-kit](https://github.com/github/spec-kit)）的分层对接层：
  - `HANDOFF.md` 模板新增"进行中的 spec"可选字段
  - `START_HERE.md` 按需加载与文件地图登记 `.specify/`
  - `SKILL.md` resume/handoff 说明如何续上 speckit 流程
  - `context-protocol.md` §5.7 写明文件分工、桥梁位置与 AGPL 不内嵌原则
  - `README.md` 新增"与 speckit 联动"整节，标注项目地址与五步联动用法

### Notes
- 不内嵌 speckit 代码：speckit 是 AGPL-3.0，本仓库 MIT，两个 skill 各自独立安装、通过文件系统对接。

## [0.1.2] - 2026-09-12

### Added
- 新增 `assets/templates/lessons.md`：项目永久教训层（L2 可选文件）
  - 与 HANDOFF 陷阱区分层：临时坑写 HANDOFF（易失），某坑重复出现第二次提升到 lessons.md（持久）
  - 三表结构：架构与边界 / 工具链与环境 / 数据与外部系统
- `init_project.py` COPY_MAP 加 lessons.md，末尾提示"初始可空"
- `doctor.py` OPTIONAL_DOCS 加 lessons.md（存在才查空模板，缺失不报错）
- `START_HERE.md` 按需加载加"动手前查 lessons"，文件地图登记 lessons
- `SKILL.md` L2 模型、init 起草列表、handoff 纪律、无 Python 降级复制清单、完成前自检同步更新
- `context-protocol.md` L2 表加 lessons，handoff SOP 加"失败路径分层"条
- `README.md` 四层表 L2 加 lessons

## [0.1.1] - 2026-09-12

### Added
- 新增 `assets/templates/glossary.md`：项目术语表（L2 可选文件）
- `init_project.py` COPY_MAP 加 glossary，AI 必做提示改为一次性集中追问关键歧义（grilling 轻量版）
- `context-protocol.md` 新增 §5.6"写给 AI 的文档原则"
- `SKILL.md` 触发表加"触发方式"列，init 起草列表加 glossary
- `doctor.py` OPTIONAL_DOCS 加 glossary

## [0.1.0] - 2026-09-12

### Added
- 首个公开版本：跨 IDE / 模型无缝开发协议
- L0 薄指针：自动生成 12 种工具的规则文件（AGENTS.md / CLAUDE.md / .cursor/rules / .windsurfrules / .trae/rules / GEMINI.md / copilot-instructions / .clinerules / .roorules / .continuerules / CONVENTIONS.md）
- L1 入口：`.ai-dev/START_HERE.md`
- L2 长期记忆：project-brief / architecture / conventions / code-index
- L3 工作记忆：HANDOFF.md + decisions/（ADR）+ archive/
- 6 个纯标准库脚本：init_project / build_index / sync_rules / archive_handoff / doctor / _common
- 无 Python 降级路径：AI 用文件工具手工等价落地
- doctor 六类腐化体检：缺文件 / L2 空模板 / 索引过时 / HANDOFF 超限 / 指针漂移 / .ai-dev 被 git 误忽略
- 机制图解 SVG
