"""Shared helpers for cross-ide-dev-context scripts.

Pure standard library, Windows/macOS/Linux safe. All text I/O must be UTF-8.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

AI_DEV_DIR = ".ai-dev"
MARKER = "cross-ide-dev-context:pointer"
BEGIN_BLOCK = "cross-ide-dev-context:begin"
END_BLOCK = "cross-ide-dev-context:end"

POINTER_BODY = """# 统一上下文入口（由 cross-ide-dev-context 自动生成，请勿在此编写项目规则）

本项目唯一事实源在 `.ai-dev/`。以下协议**自动执行，不需要用户提醒**：

- 接到任务 / 新会话开始：自动读 `.ai-dev/START_HERE.md` 与 `.ai-dev/HANDOFF.md`，
  沿 HANDOFF「下一步动作」继续；用一两句话说明你理解的现状即可，不打断用户，
  发现文档与代码矛盾时以代码为准并回写 HANDOFF。
- 动手改代码前：先查 `.ai-dev/code-index.md` 定位，并遵守 `.ai-dev/conventions.md`。
- 任务闭环 / 用户说“收工/切换/结束”时：**自动**更新 `.ai-dev/HANDOFF.md`
  （进度、下一步、陷阱、改动文件清单）；增删/移动过源码文件时按 START_HERE §3 更新 code-index.md。
- 项目规则、架构、进度一律写回 `.ai-dev/` 对应文件；不要写在本文件，本文件会被同步脚本覆盖。

<!-- {marker} -->
"""


@dataclass
class ToolSpec:
    tid: str
    rel: str                      # pointer file path relative to project root
    evidence: list[str] = field(default_factory=list)  # paths whose existence means the tool is in use
    frontmatter: str | None = None  # extra YAML frontmatter (Cursor .mdc etc.)
    note: str = ""

    @property
    def always(self) -> bool:
        return not self.evidence


# Coverage of mainstream AI coding tools. Add new tools here in one place.
TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        "agents", "AGENTS.md", note="通用开放标准（Codex CLI / Amp / Zed 等兜底入口）",
    ),
    ToolSpec(
        "claude-code", "CLAUDE.md", evidence=["CLAUDE.md", ".claude"],
        note="Anthropic Claude Code",
    ),
    ToolSpec(
        "cursor", ".cursor/rules/ai-dev-context.mdc", evidence=[".cursor"],
        frontmatter='---\ndescription: 统一上下文入口，新会话必须先读 .ai-dev/START_HERE.md\nalwaysApply: true\n---\n\n',
        note="Cursor 新版 project rules (.mdc)",
    ),
    ToolSpec(
        "cursor-legacy", ".cursorrules", evidence=[".cursorrules"],
        note="Cursor 旧版单文件规则",
    ),
    ToolSpec(
        "windsurf", ".windsurfrules", evidence=[".windsurfrules", ".codeium"],
        note="Windsurf",
    ),
    ToolSpec(
        "gemini", "GEMINI.md", evidence=["GEMINI.md", ".gemini"],
        note="Google Gemini CLI / Jules",
    ),
    ToolSpec(
        "copilot", ".github/copilot-instructions.md", evidence=[".github"],
        note="GitHub Copilot instructions",
    ),
    ToolSpec(
        "trae", ".trae/rules/ai-dev-context.md", evidence=[".trae"],
        note="Trae rules",
    ),
    ToolSpec(
        "cline", ".clinerules", evidence=[".clinerules"],
        note="Cline",
    ),
    ToolSpec(
        "roo", ".roorules", evidence=[".roorules", ".roo"],
        note="Roo Code",
    ),
    ToolSpec(
        "continue", ".continuerules", evidence=[".continuerules", ".continue"],
        note="Continue",
    ),
    ToolSpec(
        "aider", "CONVENTIONS.md", evidence=["CONVENTIONS.md"],
        note="Aider 自动读取的约定文件",
    ),
]


def find_project_root(start: Path | str | None = None) -> Path:
    """Walk upward to a directory containing .git; fall back to cwd/start itself."""
    cur = (Path(start) if start else Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists():
            return candidate
    return cur


def pointer_text(spec: ToolSpec) -> str:
    body = POINTER_BODY.format(marker=MARKER)
    if spec.frontmatter:
        return spec.frontmatter + body
    return body


def injected_block(spec: ToolSpec) -> str:
    """Block used by --inject when a hand-written rule file already exists."""
    inner = pointer_text(spec)
    return (
        f"<!-- {BEGIN_BLOCK} -->\n"
        f"{inner}\n"
        f"<!-- {END_BLOCK} -->\n"
    )


def is_own_pointer(text: str) -> bool:
    return MARKER in text


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ---------- ignore patterns (for build_index) ----------

def load_ignore_lines(root: Path) -> list[str]:
    """Load non-empty, non-comment glob lines from .gitignore and .ai-dev/ignore.conf."""
    lines: list[str] = []
    for name in (".gitignore", f"{AI_DEV_DIR}/ignore.conf"):
        p = root / name
        if not p.is_file():
            continue
        for raw in read_text(p).splitlines():
            s = raw.strip()
            if s and not s.startswith("#"):
                lines.append(s)
    return lines


def _norm(rel_parts: tuple[str, ...]) -> str:
    return "/".join(rel_parts)


def match_ignore(rel_parts: tuple[str, ...], patterns: list[str]) -> bool:
    """Match a relative path (parts) against simplified gitignore-style globs."""
    posix = _norm(rel_parts)
    name = rel_parts[-1]
    ancestors = [_norm(rel_parts[:i]) for i in range(1, len(rel_parts) + 1)]
    for pat in patterns:
        negated = pat.startswith("!")
        p = pat[1:] if negated else pat
        p = p.lstrip("/")
        dir_only = p.endswith("/")
        p = p.rstrip("/")
        if not p:
            continue
        candidates = [posix, name]
        # a directory pattern matches the directory itself and everything under it
        if dir_only:
            candidates.extend(ancestors[:-1])
        hit = any(fnmatch.fnmatch(c, p) or fnmatch.fnmatch(c, p + "/*") for c in candidates)
        if hit:
            return not negated
    return False
