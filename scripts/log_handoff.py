#!/usr/bin/env python3
"""Append one per-conversation compression record to `.ai-dev/handoffs/`.

Why this exists
---------------
A single ``HANDOFF.md`` cannot carry "compress after EVERY turn": it would grow
without bound, and two agents working in parallel would overwrite each other.
So every turn gets its own small, append-only file named

    <timestamp>-<agent>-<session>.md

and an auto-generated ``INDEX.md`` rolls them up to one line per record, so any
agent / model / session can resume in a single read. Records are never edited
in place; the index is rebuilt atomically under a lock, so concurrent writers
never clobber each other.

The compression spec every record follows:

    1. 时间戳          timestamp (ISO 8601, with timezone)
    2. Agent-会话Id    which agent, in which session, produced this
    3. 用户原话        the user's request, verbatim and complete
    4. 本轮压缩        the reply, compressed into any combination of
                      Done / Next / Verify / Risks  (all four optional,
                      but at least one must be present)

Usage (called by the AI, never by the user)::

    # 写一条记录：用户原话放文件里保证一字不删，压缩正文由 AI 写
    python scripts/log_handoff.py <项目根> \
        --user-file user.txt --body-file body.md \
        --agent claude-code --session a1b2c3d4

    # 或者用快捷参数直接拼一节
    python scripts/log_handoff.py . --user "把登录改成 JWT" \
        --done "已改 src/auth/login.py" --next "- [ ] 补刷新单测" \
        --risks "同步 requests 会阻塞事件循环"

    # 读（换 Agent / 换会话 / 换模型后恢复现场）
    python scripts/log_handoff.py . --list              # 全部条目一览
    python scripts/log_handoff.py . --tail 3            # 最近 3 条全文
    python scripts/log_handoff.py . --tail 1 --agent cursor   # 某 Agent 的最后一条
    python scripts/log_handoff.py . --template          # 打印条目模板
    python scripts/log_handoff.py . --rebuild-index     # 只重建 INDEX.md
    python scripts/log_handoff.py . --init              # 建 handoffs/ 与空 INDEX
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    AI_DEV_DIR,
    ENTRY_MARKER,
    ENTRY_TEMPLATE_NAME,
    HANDOFFS_DIR,
    INDEX_NAME,
    SCHEMA_VERSION,
    SECTIONS,
    detect_agent,
    find_project_root,
    new_session_id,
    read_text,
    write_text,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "assets" / "templates"
ENTRY_TEMPLATE = TEMPLATES / "handoff-entry.md"
USER_QUOTE_LIMIT = 4000      # 用户原话保护上限，超出仍全量写入但索引里截断显示
TAIL_DEFAULT = 3
INDEX_ROW_LIMIT = 400        # INDEX 超过此行数只保留最近的，旧的靠 --list/归档找


# ---------------------------------------------------------------- entry model

@dataclass
class Entry:
    path: Path
    name: str
    ts: str = ""                 # 2026-10-12T16:20:33+08:00
    agent: str = ""
    session: str = ""
    user_text: str = ""          # 用户原话（去引用前缀）
    sections: dict = field(default_factory=dict)   # {"Done": "...", ...}
    open_next: int = 0
    malformed: list = field(default_factory=list)  # 缺 schema / 缺用户原话 / 无压缩节
    _mtime: float = 0.0          # 排序依据，见 scan_entries

    @property
    def sort_key(self) -> str:
        return self.name

    @property
    def short_ts(self) -> str:
        # 20261012-162033-claude-code-a1b2c3d4.md -> 10-12 16:20
        m = re.match(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", self.name)
        if not m:
            return self.ts[:16] or "?"
        _, mo, d, h, mi = m.groups()
        return f"{mo}-{d} {h}:{mi}"

    @property
    def user_summary(self) -> str:
        one = " ".join(self.user_text.split())
        return one[:60] + ("…" if len(one) > 60 else "")


_SECTION_RE = re.compile(r"^###\s+(Done|Next|Verify|Risks)\s*$", re.M)
_FIELD_RE = {
    "ts": re.compile(r"^\|\s*时间戳\s*\|\s*(.+?)\s*\|\s*$", re.M),
    "agent": re.compile(r"^\|\s*Agent\s*\|\s*(.+?)\s*\|\s*$", re.M),
    "session": re.compile(r"^\|\s*会话\s*ID\s*\|\s*(.+?)\s*\|\s*$", re.M),
}


def _strip_quote(text: str) -> str:
    """Drop the leading '> ' of every quoted line, keep the original wording."""
    out = []
    for ln in text.splitlines():
        s = ln.lstrip()
        if s.startswith(">"):
            s = s[1:]
            if s.startswith(" "):
                s = s[1:]
            out.append(s)
        else:
            out.append(ln)
    return "\n".join(out).strip()


def parse_entry(path: Path) -> Entry:
    """Read one record back. Tolerant: a half-written record still parses."""
    e = Entry(path=path, name=path.name)
    text = read_text(path)
    for key, rx in _FIELD_RE.items():
        m = rx.search(text)
        if m:
            setattr(e, key, m.group(1).strip())

    m = re.search(r"^##\s+用户原话\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if m:
        e.user_text = _strip_quote(m.group(1))

    marks = list(_SECTION_RE.finditer(text))
    for i, sm in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[sm.end():end].strip()
        # a section ends at the next "## " heading too
        nxt = re.search(r"^##\s", body, re.M)
        if nxt:
            body = body[:nxt.start()].strip()
        if body:
            e.sections[sm.group(1)] = body
    if "Next" in e.sections:
        e.open_next = len(re.findall(r"^\s*-\s*\[\s*\]", e.sections["Next"], re.M))

    if ENTRY_MARKER not in text:
        e.malformed.append("缺 devctx-handoff 标记")
    if not e.user_text:
        e.malformed.append("缺用户原话")
    if not e.sections:
        e.malformed.append("无 Done/Next/Verify/Risks 任一节")
    return e


def scan_entries(handoffs: Path) -> list[Entry]:
    """All records, oldest first. INDEX/template files are not records.

    Ordered by mtime, not by filename: within the same second the collision
    suffix makes lexicographic order lie (`...-s-2.md` sorts before `...-s.md`).
    """
    if not handoffs.is_dir():
        return []
    out = []
    for p in handoffs.glob("*.md"):
        if p.name == INDEX_NAME or p.name == ENTRY_TEMPLATE_NAME:
            continue
        if p.name.startswith("0000-"):
            continue
        e = parse_entry(p)
        try:
            e._mtime = p.stat().st_mtime
        except OSError:
            e._mtime = 0.0
        out.append(e)
    out.sort(key=lambda e: (e._mtime, e.name))
    return out


# ------------------------------------------------------------------- locking

class _Lock:
    """Cross-platform advisory lock via exclusive file creation.

    Records are separate files so parallel writers never collide; the lock only
    serialises INDEX rebuilds, which read the whole directory.
    """

    def __init__(self, path: Path, timeout: float = 10.0):
        self.path = path
        self.timeout = timeout
        self.acquired = False

    def __enter__(self) -> "_Lock":
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                if time.time() >= deadline:
                    # stale lock from a crashed writer: break it and retry once
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    try:
                        fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.close(fd)
                        self.acquired = True
                        return self
                    except FileExistsError:
                        print("[!] index lock busy; rebuilding anyway", file=sys.stderr)
                        return self
                time.sleep(0.05)

    def __exit__(self, *exc) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except OSError:
                pass


# -------------------------------------------------------------- index render

def render_index(entries: list[Entry]) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    shown = entries[-INDEX_ROW_LIMIT:] if len(entries) > INDEX_ROW_LIMIT else entries
    out = [
        f"<!-- {ENTRY_MARKER}: index -->",
        "",
        "# 对话压缩记录索引（自动生成，请勿手改）",
        "",
        f"- 条目数：{len(entries)} · 生成于：{now}"
        + (f" · 仅显示最近 {len(shown)} 条" if len(shown) < len(entries) else ""),
        "- 读法：新会话 / 换 Agent / 换模型 → 先读本文件定位，再读对应条目文件；",
        "  「开放 Next」列是别人还欠的活，「Risks」是已知的坑，动手前先扫一眼。",
        "- 写出一条记录：`scripts/log_handoff.py`（无技能环境按 `0000-template.md` 手工写）。",
        "",
        "| # | 时间戳 | Agent | 会话 | 用户需求（截断） | 开放Next | 条目文件 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, e in enumerate(reversed(shown), start=1):
        flag = " ⚠" if e.malformed else ""
        out.append(
            f"| {i} | {e.short_ts} | {e.agent or '?'} | {e.session or '?'} "
            f"| {e.user_summary or '（无）'} | {e.open_next or ''} "
            f"| `{e.name}`{flag} |"
        )
    out.append("")
    if len(shown) < len(entries):
        out.append(f"> 更早的 {len(entries) - len(shown)} 条已不在索引内，"
                   "用 `--list` 查看或运行 archive 归档。")
        out.append("")
    return "\n".join(out)


def rebuild_index(handoffs: Path) -> tuple[int, Path]:
    """Rewrite INDEX.md from the records on disk. Atomic: temp file + replace."""
    entries = scan_entries(handoffs)
    text = render_index(entries)
    idx = handoffs / INDEX_NAME
    tmp = handoffs / (INDEX_NAME + ".tmp")
    write_text(tmp, text)
    os.replace(str(tmp), str(idx))
    return len(entries), idx


# ------------------------------------------------------------------ writing

def quote_user(text: str) -> str:
    lines = [ln.rstrip() for ln in text.strip().splitlines()] or [""]
    return "\n".join(f"> {ln}".rstrip() for ln in lines)


def build_body(done: str | None, nxt: str | None, verify: str | None,
               risks: str | None, body: str | None, body_file: Path | None,
               force: bool) -> str:
    """Assemble the '本轮压缩' section from flags or a full body."""
    parts: list[str] = []
    for name, val in zip(SECTIONS, (done, nxt, verify, risks)):
        if val and val.strip():
            parts.append(f"### {name}\n{val.strip()}\n")
    if body_file:
        parts.append(read_text(body_file).strip() + "\n")
    elif body and body.strip():
        parts.append(body.strip() + "\n")

    if not parts:
        sys.exit("[!] 压缩内容为空：至少给一个 --done/--next/--verify/--risks，"
                 "或用 --body-file 提供正文；--template 可看格式。")

    merged = "\n".join(p.rstrip() + "\n" for p in parts).strip() + "\n"

    # 规范校验：正文里必须能认出 Done/Next/Verify/Risks 小节
    found = set(_SECTION_RE.findall(merged))
    if not found and not force:
        sys.exit("[!] 正文里找不到 `### Done` / `### Next` / `### Verify` / `### Risks` "
                 "任一标题。压缩规范要求至少标注一节（可缺省组合）；"
                 "确认要写自由格式请加 --force。")
    return merged


def write_record(root: Path, agent: str, session: str, user_text: str,
                 body: str, model: str | None = None) -> tuple[Path, str]:
    """Append one record and rebuild the index. Returns (path, session_id)."""
    handoffs = root / AI_DEV_DIR / HANDOFFS_DIR
    handoffs.mkdir(parents=True, exist_ok=True)

    if not session:
        session = new_session_id()
    now = _dt.datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    iso = now.isoformat(timespec="seconds")

    text = (
        f"<!-- devctx-schema: {SCHEMA_VERSION} -->\n"
        f"<!-- {ENTRY_MARKER}: entry -->\n\n"
        "# 对话压缩记录\n\n"
        "| 字段 | 值 |\n|---|---|\n"
        f"| 时间戳 | {iso} |\n"
        f"| Agent | {agent} |\n"
        f"| 会话 ID | {session} |\n"
        f"| 模型 | {model.strip() if model else '—'} |\n\n"
        "## 用户原话\n\n"
        f"{quote_user(user_text)}\n\n"
        "## 本轮压缩\n\n"
        f"{body.rstrip()}\n"
    )

    base = f"{stamp}-{_slug(agent)}-{_slug(session)}"
    path = handoffs / f"{base}.md"
    n = 2
    while path.exists():                      # 同一秒内同 Agent 同会话的第二次写入
        path = handoffs / f"{base}-{n}.md"
        n += 1
    write_text(path, text)

    with _Lock(handoffs / ".index.lock"):
        rebuild_index(handoffs)
    return path, session


def _slug(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", s).strip("-") or "x"


# ------------------------------------------------------------------ reading

def cmd_tail(handoffs: Path, n: int, agent: str | None) -> int:
    entries = scan_entries(handoffs)
    if agent:
        entries = [e for e in entries if e.agent == agent]
    if not entries:
        print("[!] 没有符合条件的对话记录。")
        return 1
    for e in entries[-n:]:
        print(f"===== {e.name} =====")
        print(read_text(e.path).rstrip())
        print()
    return 0


def cmd_list(handoffs: Path) -> int:
    entries = scan_entries(handoffs)
    if not entries:
        print("[!] handoffs/ 里还没有记录。")
        return 1
    print(f"{len(entries)} 条记录（旧 → 新）：\n")
    for e in entries:
        flag = "  [格式待修]" if e.malformed else ""
        print(f"  {e.short_ts}  {e.agent:<14} {e.session:<10} "
              f"open:{e.open_next or '-'}  {e.user_summary}{flag}")
    return 0


def cmd_init(root: Path) -> int:
    handoffs = root / AI_DEV_DIR / HANDOFFS_DIR
    handoffs.mkdir(parents=True, exist_ok=True)
    (handoffs / "archive").mkdir(exist_ok=True)
    tpl = handoffs / ENTRY_TEMPLATE_NAME
    if not tpl.exists() and ENTRY_TEMPLATE.is_file():
        write_text(tpl, read_text(ENTRY_TEMPLATE))
        print(f"[+] {AI_DEV_DIR}/{HANDOFFS_DIR}/{ENTRY_TEMPLATE_NAME}")
    n, idx = rebuild_index(handoffs)
    print(f"[+] {AI_DEV_DIR}/{HANDOFFS_DIR}/{INDEX_NAME}（{n} 条）")
    return 0


def cmd_template() -> int:
    if ENTRY_TEMPLATE.is_file():
        print(read_text(ENTRY_TEMPLATE).rstrip())
        return 0
    print("[!] 找不到 assets/templates/handoff-entry.md", file=sys.stderr)
    return 1


# --------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", default=".", help="项目目录（默认当前目录）")
    ap.add_argument("--agent", help="写入方 Agent 名（默认读 DEVCTX_AGENT，再按在用工具探测）")
    ap.add_argument("--session", help="会话 ID（默认读 DEVCTX_SESSION_ID，否则自动生成）")
    ap.add_argument("--model", help="模型名，可选")
    ap.add_argument("--user", help="用户原话（短消息直接传；长消息用 --user-file）")
    ap.add_argument("--user-file", help="用户原话文件路径，'-' 表示标准输入")
    ap.add_argument("--done", help="Done：已经做了什么")
    ap.add_argument("--next", dest="nxt", help="Next：下一步做什么")
    ap.add_argument("--verify", help="Verify：要跑什么测试")
    ap.add_argument("--risks", help="Risks：有什么坑")
    ap.add_argument("--body", help="本轮压缩正文（含 ### Done 等标题）")
    ap.add_argument("--body-file", help="本轮压缩正文文件")
    ap.add_argument("--force", action="store_true", help="允许没有标准小节标题的自由格式")
    ap.add_argument("--template", action="store_true", help="打印条目模板")
    ap.add_argument("--list", action="store_true", help="列出全部记录")
    ap.add_argument("--tail", type=int, metavar="N", help=f"读最近 N 条全文（默认 {TAIL_DEFAULT}）")
    ap.add_argument("--rebuild-index", action="store_true", help="只重建 INDEX.md")
    ap.add_argument("--init", action="store_true", help="创建 handoffs/ 与空 INDEX")
    args = ap.parse_args()

    if args.template:
        return cmd_template()

    root = find_project_root(args.project)
    handoffs = root / AI_DEV_DIR / HANDOFFS_DIR

    if args.init:
        return cmd_init(root)
    if args.list:
        return cmd_list(handoffs)
    if args.tail is not None:
        return cmd_tail(handoffs, args.tail, args.agent)
    if args.rebuild_index:
        if not handoffs.is_dir():
            print(f"[!] {handoffs} 不存在，先 --init。", file=sys.stderr)
            return 1
        n, idx = rebuild_index(handoffs)
        print(f"[+] 重建 {idx.relative_to(root)}（{n} 条）")
        return 0

    # ---- write a record ----
    if args.user_file:
        user_text = (sys.stdin.read() if args.user_file == "-"
                     else read_text(Path(args.user_file)))
    elif args.user:
        user_text = args.user
    else:
        sys.exit("[!] 必须提供用户原话：--user \"...\" 或 --user-file 路径。"
                 "压缩规范要求每条记录都带完整需求。")

    body_file = Path(args.body_file) if args.body_file else None
    body = build_body(args.done, args.nxt, args.verify, args.risks,
                      args.body, body_file, args.force)

    agent = detect_agent(root, args.agent)
    session = args.session or os.environ.get("DEVCTX_SESSION_ID", "").strip()
    path, session = write_record(root, agent, session, user_text, body, args.model)

    rel = path.relative_to(root)
    print(f"[+] {rel}")
    print(f"    agent={agent} session={session}"
          + (f" model={args.model.strip()}" if args.model else ""))
    print(f"    INDEX.md 已重建；新会话/换 Agent 后读 `--tail {TAIL_DEFAULT}` 即可接续。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
