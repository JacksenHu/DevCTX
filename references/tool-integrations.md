# 各 AI 编程工具集成参考

## 0. 先分清两种角色

- **主控 AI**：装有 cross-ide-dev-context 技能的一方（如豆包），负责初始化、重建索引、体检、同步指针。
- **协作 AI**：其他 IDE 里的 AI，不装本技能，只靠项目内自包含的 `.ai-dev/` 与指针文件工作。
因此指针与 `.ai-dev/` 文件不得引用“某脚本路径”这类只有主控才有的东西；需要脚本的动作都要在
START_HERE 给出“无技能时手工等价完成”的降级说明。用户全程不需要手动执行命令。

## 1. 已覆盖工具映射（脚本自动维护指针）

| 工具 | 指针文件（相对项目根） | 自动加载条件 / 注意事项 |
|---|---|---|
| 通用兜底（Codex CLI、Amp、Zed 等） | `AGENTS.md` | 开放约定，detected 模式也总是生成 |
| Claude Code | `CLAUDE.md` | 会话启动自动读取；本技能只接管根级 |
| Cursor（新版） | `.cursor/rules/ai-dev-context.mdc` | 带 `alwaysApply: true`；Settings → Rules 可见 |
| Cursor（旧版） | `.cursorrules` | 仅项目已存在该文件时生成，避免双加载 |
| Windsurf | `.windsurfrules` | 启动工作区自动加载 |
| Gemini CLI / Jules | `GEMINI.md` | 会话启动自动读取 |
| GitHub Copilot | `.github/copilot-instructions.md` | 需 VS Code 启用 Instruction Files；`.github/` 存在才生成 |
| Trae / Trae Work CN | `.trae/rules/ai-dev-context.md` | 项目规则目录下任意 .md 自动生效；详见 §1.1 |
| Cline | `.clinerules` | 自动读取 |
| Roo Code | `.roorules` | 自动读取 |
| Continue | `.continuerules` | 较新版本支持 |
| Aider | `CONVENTIONS.md` | 自动读入；也可 `aider --read .ai-dev/START_HERE.md` |

detected 模式只给“项目里出现证据（.cursor/、.github/、.trae/、CLAUDE.md 等）”的工具生成，
外加 AGENTS.md；`--all` 铺全部。新增工具只改 `scripts/_common.py` 的 `TOOL_SPECS`。

### 1.1 Trae / Trae Work CN 专项（据 docs.trae.ai 核实）

- 项目规则固定在**项目根 `.trae/rules/`**，任意 .md 文件名自动加载；全局规则在 `%userprofile%/.trae/user_rules`。
- Trae Work **桌面版兼容根目录 `AGENTS.md`**，但默认不加载，需手动开：
  **设置 → 规则 → 导入设置 →「将 AGENTS.md 包含在上下文中」**。此开关只是双保险：
  `.trae/rules/` 指针已自动生效，**用户不开开关也能正常工作，不要把它说成必做步骤**。
- 规则环境分本地/云端：桌面版本地任务读本地文件；GitHub 拉取的云端任务走云端规则环境。
- 新建/修改规则后**开新对话**生效；对话中可用 `#Rule` 显式引用某条规则。

## 2. 指针策略：只写“路标”不复制规则

工具私有文件只放十几行自动协议（自动 resume、按索引定位、闭环自动 handoff）：
- 规则只在 `.ai-dev/` 维护一份 → 改一次全部工具生效，杜绝 N 份漂移；
- 指针极短 → 固定开销几乎为零；
- 同步幂等：纯指针可随时刷新，含手写内容的文件默认**绝不覆盖**。

## 3. 手写规则文件的处理（--inject）

工具文件已有真实规则（如团队既有 CLAUDE.md）时：
- 默认 `sync_rules.py` 跳过并报 conflict，不破坏内容；
- `--inject` 插入 `<!-- cross-ide-dev-context:begin/end -->` 包裹的指针块，块外原样保留，可重复执行；
- 随后建议把块外规则的长期内容迁回 `.ai-dev/conventions.md`，保持单一事实源。

## 4. 不支持自动加载规则的工具 / 任意 Work 软件（兜底）

不能自动读规则文件的 IDE、云端 work 环境、对话助手，用**开场白挂载**，新会话第一条发：

```text
先读 .ai-dev/START_HERE.md 和 .ai-dev/HANDOFF.md，按其中协议恢复上下文，用一两句对齐现状后直接继续。
```

连工作区文件访问都没有（纯网页对话）时，把这两个文件内容贴进首条消息，仍远小于让它通读全代码库。

## 5. 团队协作、CI 与体检

- `.ai-dev/` 与全部指针**提交 git**，作为跨人跨工具的共享项目记忆；doctor 会警告“`.ai-dev` 被 .gitignore 误忽略”。
- 指针由脚本统一生成，合并冲突时以重新生成结果为准。
- CI 可加（退出码非 0 即拦截）：
  `python scripts/sync_rules.py --check`（指针缺失/漂移，退出码 2）；
  `python scripts/doctor.py`（L2 空模板、索引过时、HANDOFF 超限等，退出码 1/2）。
- HANDOFF 若要私有，把 `.ai-dev/HANDOFF.md` 加入 `.gitignore` 并约定各自维护；单人多工具建议提交，换机也能接续。
