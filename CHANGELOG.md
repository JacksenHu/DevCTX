# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
