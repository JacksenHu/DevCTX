# Conventions — 开发约定（所有工具统一遵守）

> 本文件是跨 IDE / 跨模型的**统一行为规范**。任何工具生成的代码都必须满足这里的硬性约定；
> 与本文件冲突的"模型个人习惯"一律无效。约定要少而硬，只写真正会反复踩的规则，不写语言教科书。

## 代码风格

- 格式化工具与版本：<!-- 例: prettier 3.x / black，配置文件位置 -->
- 命名：<!-- 例: 文件 kebab-case；类 PascalCase；函数/变量 snake_case；布尔值以 is/has/can 开头 -->
- 函数与文件体量：<!-- 例: 单函数 ≤ 80 行，超出先拆 -->

## 工程结构约束

- 分层依赖方向：<!-- 例: controller → service → repository，禁止反向 import -->
- 公共能力放哪：<!-- 例: 通用工具进 src/common，业务代码不得反向依赖 -->
- 禁止事项：
  - <!-- 例: 禁止在循环里发网络请求；禁止吞异常；禁止提交 print/console 调试输出 -->

## 错误处理与日志

- 错误处理：<!-- 例: 边界层统一转译错误，内部抛带语义的自定义异常 -->
- 日志：<!-- 级别使用规则、禁止打印敏感字段 -->

## 测试要求

- <!-- 例: 新业务逻辑必须带单测；bug 修复先写复现测试；测试命名 test_行为_条件_结果 -->
- 覆盖率底线：

## 分支与提交

- 分支命名：<!-- 例: feature/ login-refactor, fix/ null-token-crash, chore/ bump-deps -->
- 提交信息（Conventional Commits）：
  ```text
  feat(auth): add JWT refresh token endpoint
  fix(login): prevent null pointer on expired session
  refactor(db): extract user repository
  test(auth): add unit tests for token refresh
  docs(readme): update install instructions
  chore: bump ruff to 0.4
  ```
- 提交粒度：<!-- 例: 一次提交只做一件事，禁止混合格式化与逻辑改动 -->

## AI 协作特别约定（重要）

- 改动前先在 `code-index.md` 定位，改动后若增删/移动文件，必须重建索引。
- 难以逆转的决定先写 `decisions/` ADR，再动手。
- 不擅自升级依赖版本、不引入新依赖，除非任务明确要求或经用户同意。
- 不修改与当前任务无关的文件；发现的既有问题只记录到 HANDOFF「陷阱」或单独提出。
- 每次收工更新 HANDOFF.md，保证下一个工具能无缝接续。
