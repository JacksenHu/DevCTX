"""Shared helpers for devctx scripts.

Pure standard library, Windows/macOS/Linux safe. All text I/O must be UTF-8.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

AI_DEV_DIR = ".ai-dev"
HANDOFFS_DIR = "handoffs"
DOCS_DIR = "docs"
INDEX_NAME = "INDEX.md"
DOC_INDEX_NAME = "DOCS.md"
DOC_TEMPLATE_NAME = "0000-template.md"
ENTRY_TEMPLATE_NAME = "0000-template.md"
MARKER = "devctx:pointer"
BEGIN_BLOCK = "devctx:begin"
END_BLOCK = "devctx:end"
SCHEMA_VERSION = "v2"
ENTRY_MARKER = "devctx-handoff"
SECTIONS = ("Done", "Next", "Verify", "Risks", "Intent")

POINTER_BODY = """# 统一上下文入口（由 devctx 自动生成，请勿在此编写项目规则）

本项目唯一事实源在 `.ai-dev/`。以下协议**自动执行，不需要用户提醒**：

- 接到任务 / 新会话开始：自动读 `.ai-dev/START_HERE.md`、`.ai-dev/HANDOFF.md`
  与 `.ai-dev/handoffs/INDEX.md`（每轮对话压缩流水索引），沿 HANDOFF「下一步动作」继续；
  用一两句话说明你理解的现状即可，不打断用户；发现文档与代码矛盾时以代码为准并回写。
- **动手前先做需求受理**：先查 `.ai-dev/docs/DOCS.md`（文档索引）与 handoffs 未闭环项，
  判定本次是"改现有文档"还是"真的新建"。**一个需求只对应一份文档**，
  用户插话/追加/更正都是改现有文档，严禁按对话轮次新建文档。
- **每轮对话结束（你输出回复之后）**：自动往 `.ai-dev/handoffs/` 追加一条压缩记录
  （时间戳 + Agent-会话Id + 用户原话 + Done/Next/Verify/Risks），并同步更新 `HANDOFF.md`。
- 动手改代码前：先查 `.ai-dev/code-index.md` 定位，并遵守 `.ai-dev/conventions.md`。
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


# ---------- agent identity (for per-conversation handoff records) ----------

def detect_tool_ids(root: Path) -> list[str]:
    """Tool ids whose evidence exists in the project, in TOOL_SPECS order.

    Used to guess *which* agent is writing a handoff record when it does not
    say so itself. `agents` (AGENTS.md) is a generic fallback, never a guess.
    """
    return [s.tid for s in TOOL_SPECS
            if not s.always and any((root / ev).exists() for ev in s.evidence)]


def detect_agent(root: Path, explicit: str | None = None) -> str:
    """Resolve the writing agent: explicit flag > DEVCTX_AGENT > detected tool."""
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("DEVCTX_AGENT", "").strip()
    if env:
        return env
    found = detect_tool_ids(root)
    return found[0] if found else "unknown"


def new_session_id() -> str:
    """Short, collision-resistant id for one conversation.

    Deliberately not a UUID: it lands in a filename and in every INDEX row, so
    it must stay readable. The writing agent reuses it for the whole session.
    """
    import secrets
    return secrets.token_hex(4)


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


def _glob_to_regex(pat: str) -> "re.Pattern[str]":
    """Translate a gitignore-ish glob to an anchored regex.

    Supported: ``*`` (one path segment), ``?`` (one char), ``**`` (any number of
    segments, including zero), ``**/`` (zero or more leading directories).
    """
    i, n = 0, len(pat)
    out: list[str] = []
    while i < n:
        c = pat[i]
        if c == "*":
            j = i
            while j < n and pat[j] == "*":
                j += 1
            stars = j - i
            if stars >= 2:
                if j < n and pat[j] == "/":
                    out.append(r"(?:.*/)?")  # **/ -> zero or more leading dirs
                    i = j + 1
                else:
                    out.append(r".*")        # ** -> anything, including "/"
                    i = j
            else:
                out.append(r"[^/]*")         # * -> within one segment
                i = j
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _pattern_hit(posix: str, name: str, ancestors: list[str],
                 p: str, anchored: bool) -> bool:
    """Does pattern `p` cover this path?

    `posix` is the full relative path, `name` its last segment, `ancestors` the
    relative paths of all parent directories. A pattern without a slash matches
    a bare segment at any depth (gitignore semantics); one containing a slash is
    matched against full paths.
    """
    rx = _glob_to_regex(p)
    if anchored:
        # "/build" only ever matches at the repository root
        if rx.match(posix):
            return True
        return any(rx.match(a) for a in ancestors if "/" not in a)
    if "/" in p:
        if rx.match(posix):
            return True
        # "assets/third-party/" also covers everything beneath that directory
        return any(rx.match(a) for a in ancestors)
    # bare name: matches the file/dir itself, or any ancestor segment of that name
    if rx.match(name):
        return True
    return any(rx.match(a.rsplit("/", 1)[-1]) for a in ancestors)


def match_ignore(rel_parts: tuple[str, ...], patterns: list[str]) -> bool:
    """Match a relative path (parts) against gitignore-style globs.

    Later patterns win, so ``!negation`` re-includes what an earlier rule excluded.
    A trailing ``/`` marks a directory pattern (it then also covers everything
    beneath that directory). A leading ``/`` anchors the pattern to the root.
    """
    posix = _norm(rel_parts)
    name = rel_parts[-1]
    ancestors = [_norm(rel_parts[:i]) for i in range(1, len(rel_parts))]
    ignored = False
    for pat in patterns:
        negated = pat.startswith("!")
        p = pat[1:] if negated else pat
        anchored = p.startswith("/")
        p = p.lstrip("/").rstrip("/")
        if not p:
            continue
        if _pattern_hit(posix, name, ancestors, p, anchored):
            # later rules override earlier ones, so a trailing "!" re-includes
            ignored = not negated
    return ignored
