#!/usr/bin/env python3
"""Document registry and requirement intake for a devctx-onboarded project.

Two problems this solves
------------------------
1. **Documents breed without control.** Every new requirement produces yet
   another file, the docs folder fills up, and the next agent cannot tell which
   file is authoritative — so it guesses, and the guess drifts.
   Fix: ONE directory (`.ai-dev/docs/`) with ONE index (`DOCS.md`). A document
   may only be created after the index says nothing already owns the topic, and
   one requirement maps to exactly one document for its whole life — including
   when the user interrupts, appends, or corrects mid-conversation.

2. **Requirements are taken at face value.** The user speaks casually: the
   feature may already be built, half-built, or being reworded right now. An
   agent that trusts the phrasing starts a fresh document and drifts.
   Fix: an intake step that resolves the utterance against evidence —
   open `Next` items in `handoffs/`, unfinished `specs/*/tasks.md`, the docs
   index, and the code map — and only then decides NEW / EXTEND / FIX /
   CONTINUE / CORRECTION. The verdict is recorded in the handoff record so the
   next agent does not re-litigate it.

Usage (called by the AI, never by the user)::

    # 受理裁定：动手前必跑，回答"改现有文档还是新建"
    python scripts/doc_registry.py <根> --intake --user-file user.txt
    python scripts/doc_registry.py <根> --find 登录        # 找现有文档

    # 登记与维护
    python scripts/doc_registry.py <根> --list
    python scripts/doc_registry.py <根> --add docs/auth-jwt.md --topic "登录鉴权" \
        --source "NEW 2026-10-12" [--status active]
    python scripts/doc_registry.py <根> --rebuild
    python scripts/doc_registry.py <根> --check           # 孤儿/漂移/重复主题
    python scripts/doc_registry.py <根> --archive docs/old.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    AI_DEV_DIR,
    DOCS_DIR,
    DOC_INDEX_NAME,
    DOC_TEMPLATE_NAME,
    find_project_root,
    read_text,
    write_text,
)
import log_handoff  # noqa: E402

TEMPLATES = Path(__file__).resolve().parent.parent / "assets" / "templates"
DOC_TEMPLATE = TEMPLATES / "doc-template.md"
STATUSES = ("active", "stable", "archived")
STRONG_NGRAM = 3          # 3-4 字命中视为强相关
INTAKE_THRESHOLD = 3.0    # 相关度分值阈值


# ------------------------------------------------------------------ doc model

@dataclass
class Doc:
    path: Path
    name: str
    topic: str = ""
    status: str = ""
    source: str = ""
    updated: str = ""
    links: int = 0
    malformed: list = field(default_factory=list)

    @property
    def is_archived(self) -> bool:
        return self.status == "archived"


_FIELD_RES = {
    "topic": re.compile(r"^\|\s*主题\s*\|\s*(.+?)\s*\|\s*$", re.M),
    "status": re.compile(r"^\|\s*状态\s*\|\s*([a-z]+)", re.M),
    "source": re.compile(r"^\|\s*来源需求\s*\|\s*(.+?)\s*\|\s*$", re.M),
    "updated": re.compile(r"^\|\s*最近更新\s*\|\s*(\d{4}-\d{2}-\d{2})", re.M),
}


def parse_doc(path: Path) -> Doc:
    d = Doc(path=path, name=path.name)
    text = read_text(path)
    for key, rx in _FIELD_RES.items():
        m = rx.search(text)
        if m:
            setattr(d, key, m.group(1).strip())
    if not d.topic:
        # 主题行缺失时用 H1 兜底：老文档或手写文档仍可被索引和受理匹配到
        m = re.search(r"^#\s+(.+)$", text, re.M)
        if m:
            d.topic = m.group(1).strip()
    if "devctx-docs: doc" not in text:
        d.malformed.append("缺 devctx-docs 标记")
    if not d.topic:
        d.malformed.append("缺主题")
    if not d.source:
        d.malformed.append("缺来源需求")
    return d


def scan_docs(docs_dir: Path) -> list[Doc]:
    if not docs_dir.is_dir():
        return []
    out = [parse_doc(p) for p in sorted(docs_dir.glob("*.md"), key=lambda x: x.name)
           if p.name != DOC_INDEX_NAME and not p.name.startswith("0000-")]
    return out


# ------------------------------------------------------------------ rendering

def render_index(docs: list[Doc]) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    live = [d for d in docs if not d.is_archived]
    out = [
        "<!-- devctx-docs: index -->",
        "",
        "# 项目文档索引（单一事实源）",
        "",
        f"- 文档数：{len(live)}（另有 {len(docs) - len(live)} 份已归档） · 生成于：{now}",
        "- **新建任何文档前，必须先查本文件。** 能并进现有文档的，禁止新建。",
        "- **一个需求只对应一份文档**，按主题组织，严禁按对话轮次组织（“登录-第2次.md” 是反模式）。",
        "- 用户中途插话 / 输出后追加要求 / 发现理解错了 → 都是**修改现有文档**，不是新建。",
        "",
        "| 文档 | 主题 | 状态 | 来源需求 | 最近更新 | 关联记录数 |",
        "|---|---|---|---|---|---|",
    ]
    for d in docs:
        flag = " ⚠" if d.malformed else ""
        out.append(
            f"| `{d.name}` | {d.topic or '（缺）'} | {d.status or '?'} "
            f"| {d.source or '（缺）'} | {d.updated or '?'} | {d.links}{flag} |"
        )
    out.append("")
    if not docs:
        out.append("> 还没有文档。第一个需求出现时照 `0000-template.md` 新建，并立刻登记进来。")
        out.append("")
    return "\n".join(out)


def rebuild_index(docs_dir: Path) -> tuple[int, Path]:
    # 关联记录数必须在这里算：scan 出全新 Doc 对象，调用方预设的 links 会被丢弃
    docs = scan_docs(docs_dir)
    root = docs_dir.parent.parent          # .ai-dev/docs -> 项目根
    for d in docs:
        d.links = count_links(root, d.name)
    idx = docs_dir / DOC_INDEX_NAME
    tmp = docs_dir / (DOC_INDEX_NAME + ".tmp")
    write_text(tmp, render_index(docs))
    os.replace(str(tmp), str(idx))
    return len(docs), idx


def count_links(root: Path, doc_name: str) -> int:
    """How many handoff records reference this document."""
    handoffs = root / AI_DEV_DIR / "handoffs"
    if not handoffs.is_dir():
        return 0
    n = 0
    for p in handoffs.glob("*.md"):
        if p.name in (log_handoff.INDEX_NAME, log_handoff.ENTRY_TEMPLATE_NAME):
            continue
        if doc_name in read_text(p):
            n += 1
    return n


# ------------------------------------------------------------- term matching

def _terms(text: str) -> set[str]:
    """Matchable terms: ASCII words plus CJK 2/3/4-grams.

    No jieba dependency — n-grams are enough to tell "登录" from "注册".
    """
    out: set[str] = set()
    for w in re.findall(r"[A-Za-z0-9_.\-]{2,}", text):
        out.add(w.lower())
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                out.add(run[i:i + n])
    return out


def _score(user_terms: set[str], doc_terms: set[str]) -> float:
    shared = user_terms & doc_terms
    return sum(len(t) for t in shared) / 2.0


# -------------------------------------------------------------------- intake

VERDICTS = {
    "NEW": "全新功能：现有文档、进行中记录、specs 均未覆盖，才允许新建文档",
    "EXTEND": "扩展已有功能：改现有文档，禁止新建",
    "FIX": "修复既有行为：定位到规定该行为的那份文档，改它",
    "CONTINUE": "续做半成品：接着 handoffs 里未闭环的 Next 干，不新建文档",
    "CORRECTION": "更正/追加：用户中途插话或输出后补要求，并进当前需求文档",
}


def intake(root: Path, user_text: str) -> int:
    """Print an evidence-based verdict on what this utterance actually means."""
    docs_dir = root / AI_DEV_DIR / DOCS_DIR
    docs = scan_docs(docs_dir)
    user_terms = _terms(user_text)

    print(f"需求受理裁定 — {root}\n")
    print(f"用户原话：{' '.join(user_text.split())[:120]}\n")

    evidence: list[tuple[str, str, float]] = []

    # 1) 未闭环的 handoffs：最可能的"续做 / 更正"信号。
    #    阈值取 INTAKE_THRESHOLD：任意两字重合都算命中会把"导出报表"误判成
    #    "继续做登录"，那正是要靠受理步骤避免的漂移。
    open_records = []
    for e in log_handoff.scan_entries(root / AI_DEV_DIR / "handoffs"):
        if e.open_next or "Next" in e.sections:
            s = _score(user_terms, _terms(e.user_text + " " + e.sections.get("Next", "")))
            if s >= INTAKE_THRESHOLD:
                open_records.append((s, e))
    open_records.sort(key=lambda x: -x[0])
    for s, e in open_records[:3]:
        evidence.append(("handoffs 未闭环",
                         f"{e.name} 开放 Next {e.open_next} 条（{e.agent}/{e.session}）"
                         f" 相关度 {s:.1f}", s))

    # 2) specs 未闭环任务
    specs_hit = []
    specs_dir = root / "specs"
    if specs_dir.is_dir():
        for tasks in sorted(specs_dir.glob("*/tasks.md")):
            text = read_text(tasks)
            s = _score(user_terms, _terms(text))
            if s > 0:
                done = len(re.findall(r"^\s*-\s*\[x\]", text, re.M))
                total = len(re.findall(r"^\s*-\s*\[[ x]\]", text, re.M))
                specs_hit.append((s, tasks, done, total))
    specs_hit.sort(key=lambda x: -x[0])
    for s, tasks, done, total in specs_hit[:2]:
        evidence.append(("specs 未闭环",
                         f"{tasks.relative_to(root)} 第 {done}/{total} 条 相关度 {s:.1f}", s))

    # 3) 现有文档
    doc_hits = []
    for d in docs:
        if d.is_archived:
            continue
        s = _score(user_terms, _terms(d.topic + " " + d.name))
        if s > 0:
            doc_hits.append((s, d))
    doc_hits.sort(key=lambda x: -x[0])
    for s, d in doc_hits[:3]:
        evidence.append(("现有文档",
                         f"docs/{d.name} 「{d.topic}」[{d.status}] 相关度 {s:.1f}", s))

    for kind, detail, _s in evidence:
        print(f"  [{kind}] {detail}")
    if not evidence:
        print("  （未命中任何现有文档、未闭环记录或 specs）")

    best_doc = doc_hits[0][1] if doc_hits else None
    best_rec = open_records[0][1] if open_records else None
    print()

    # 优先级：active 文档 > stable 文档 > 未闭环记录 > specs。
    # 文档是需求的长期事实源，记录只是过程流水；有主文档就必须落到文档上，
    # 否则"改需求"会被当成"新任务"重新建一份文档——这正是特性漂移的起点。
    if best_doc and best_doc.status == "active":
        verdict = "EXTEND"
        target = f"docs/{best_doc.name}"
        why = (f"已有进行中文档「{best_doc.topic}」覆盖该主题——**改它，禁止新建**。"
               "在文档「需求变更」里追加本次原话，再动手。")
    elif best_rec:
        verdict = "CONTINUE"
        target = best_rec.name
        why = (f"「{best_rec.name}」有未闭环的 Next（{best_rec.open_next} 条），"
               "但还没有文档登记这个需求——先接着做，并**补一份文档**把需求定下来。")
    elif best_doc:
        verdict = "EXTEND"
        target = f"docs/{best_doc.name}"
        why = (f"已有文档「{best_doc.topic}」（状态 {best_doc.status}）覆盖该主题——"
               "若需求有变，改这份并把状态改回 active；若确属全新主题，换主题词重查。")
    elif specs_hit:
        verdict = "CONTINUE"
        target = str(specs_hit[0][1].relative_to(root))
        why = "specs 里有未闭环任务——按 tasks.md 继续，不要另起文档。"
    else:
        verdict = "NEW"
        target = "docs/<领域>-<主题>.md"
        why = ("三类证据均未覆盖。**新建前先 `--find` 用其他关键词再查一次**；"
               "确认无主后照 `docs/0000-template.md` 建文档并登记进 DOCS.md。")

    print(f"建议判定：**{verdict}** → {target}")
    print(f"含义：{VERDICTS[verdict]}")
    print(f"理由：{why}\n")

    print("下一步（按顺序）：")
    if verdict == "NEW":
        print(f"  1. 跑 `--find <主题词>` 二次确认没有主文档")
        print(f"  2. 复制 `docs/0000-template.md` 为 `{target}`，填需求原文与当前结论")
        print(f"  3. 跑 `--add {target} --topic \"<主题>\" --source \"NEW <日期>\"` 登记")
        print(f"  4. 动手实现；收工时在 handoff 记录写 `### Intent`："
              f"NEW → {target}")
    else:
        print(f"  1. 读 {target}，把用户原话并进它的「需求变更」小节")
        print(f"  2. 对照「当前结论」与代码现状，确认是要改还是要修")
        print(f"  3. 动手实现；收工时在 handoff 记录写 `### Intent`："
              f"{verdict} → {target}")
    print(f"  4. 用户中途插话/输出后又提要求 → 仍是 {verdict} 并进同一份文档，"
          "绝不新建")
    return 0


def cmd_find(root: Path, keyword: str) -> int:
    docs_dir = root / AI_DEV_DIR / DOCS_DIR
    terms = _terms(keyword)
    hits = []
    for d in scan_docs(docs_dir):
        s = _score(terms, _terms(d.topic + " " + d.name))
        if s > 0:
            hits.append((s, d))
    hits.sort(key=lambda x: -x[0])
    if not hits:
        print(f"[!] 没有文档命中「{keyword}」——该主题可能还没有主文档（新建前请再换个词查一次）。")
        return 1
    print(f"命中「{keyword}」的文档：\n")
    for s, d in hits:
        print(f"  {s:5.1f}  docs/{d.name}  [{d.status}] {d.topic}")
    return 0


def cmd_add(root: Path, rel: str, topic: str, source: str, status: str) -> int:
    docs_dir = root / AI_DEV_DIR / DOCS_DIR
    p = root / rel
    if not p.is_file():
        print(f"[!] {rel} 不存在，先照 docs/0000-template.md 创建。", file=sys.stderr)
        return 1
    text = read_text(p)
    text = re.sub(r"^\|\s*主题\s*\|.*$", f"| 主题 | {topic} |", text, count=1, flags=re.M)
    text = re.sub(r"^\|\s*状态\s*\|.*$", f"| 状态 | {status} |", text, count=1, flags=re.M)
    text = re.sub(r"^\|\s*来源需求\s*\|.*$", f"| 来源需求 | {source} |", text, count=1, flags=re.M)
    text = re.sub(r"^\|\s*最近更新\s*\|.*$",
                  f"| 最近更新 | {_dt.date.today().isoformat()} |", text, count=1, flags=re.M)
    write_text(p, text)
    n, idx = rebuild_index(docs_dir)
    print(f"[+] 已登记 {rel}（主题「{topic}」· {status}）")
    print(f"    DOCS.md 已重建（{n} 份文档）")
    return 0


def cmd_archive(root: Path, rel: str) -> int:
    docs_dir = root / AI_DEV_DIR / DOCS_DIR
    p = root / rel
    if not p.is_file():
        print(f"[!] {rel} 不存在。", file=sys.stderr)
        return 1
    dest_dir = root / AI_DEV_DIR / "archive" / DOCS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{p.stem}-{ts}.md"
    text = read_text(p)
    text = re.sub(r"^\|\s*状态\s*\|.*$", "| 状态 | archived |", text, count=1, flags=re.M)
    write_text(dest, text)
    p.unlink()
    n, _ = rebuild_index(docs_dir)
    print(f"[+] {rel} -> archive/{DOCS_DIR}/{dest.name}；DOCS.md 已重建（{n} 份）。")
    return 0


def cmd_check(root: Path) -> int:
    docs_dir = root / AI_DEV_DIR / DOCS_DIR
    if not docs_dir.is_dir():
        print(f"[!] {AI_DEV_DIR}/{DOCS_DIR}/ 不存在；init 未铺或已被删。")
        return 1
    docs = scan_docs(docs_dir)
    idx = docs_dir / DOC_INDEX_NAME
    problems = 0

    on_disk = {d.name for d in docs}
    in_index: set[str] = set()
    if idx.is_file():
        in_index = set(re.findall(r"^\|\s*`([^`]+\.md)`", read_text(idx), re.M))
    orphans = sorted(on_disk - in_index)
    ghosts = sorted(in_index - on_disk)
    if orphans:
        problems += len(orphans)
        print(f"[XX] 未登记文档（必须先登记才能存在）：{', '.join(orphans)}")
    if ghosts:
        problems += len(ghosts)
        print(f"[XX] 索引里登记了但不存在的文档：{', '.join(ghosts)}")
    for d in docs:
        if d.malformed:
            problems += 1
            print(f"[XX] {d.name} 不合规：{', '.join(d.malformed)}")
        if not d.is_archived and d.links == 0:
            print(f"[! ] {d.name} 没有任何 handoff 记录引用它（来源需求不可追溯）")

    # 重复主题：两份文档讲同一件事，正是"文档爆掉"的起点
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            if a.is_archived or b.is_archived:
                continue
            if _score(_terms(a.topic), _terms(b.topic)) >= 6.0:
                problems += 1
                print(f"[XX] 主题重复：{a.name} 与 {b.name} 都在讲「{a.topic}」，应合并")

    if not problems:
        print(f"[OK] {len(docs)} 份文档全部登记在案、格式合规、无重复主题。")
        return 0
    print(f"\n{problems} 处问题；`--rebuild` 可修索引漂移，其余需 AI 合并/归档。")
    return 2


def cmd_init(root: Path) -> int:
    docs_dir = root / AI_DEV_DIR / DOCS_DIR
    docs_dir.mkdir(parents=True, exist_ok=True)
    tpl = docs_dir / DOC_TEMPLATE_NAME
    if not tpl.exists() and DOC_TEMPLATE.is_file():
        write_text(tpl, read_text(DOC_TEMPLATE))
        print(f"[+] {AI_DEV_DIR}/{DOCS_DIR}/{DOC_TEMPLATE_NAME}")
    n, _ = rebuild_index(docs_dir)
    print(f"[+] {AI_DEV_DIR}/{DOCS_DIR}/{DOC_INDEX_NAME}（{n} 份文档）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", default=".", help="项目目录（默认当前目录）")
    ap.add_argument("--intake", action="store_true",
                    help="需求受理裁定：读用户原话，判定该改现有文档还是新建")
    ap.add_argument("--user", help="用户原话（配合 --intake）")
    ap.add_argument("--user-file", help="用户原话文件，'-' 表示标准输入")
    ap.add_argument("--find", metavar="关键词", help="按主题词找现有文档")
    ap.add_argument("--add", metavar="文件", help="登记一份文档（相对项目根）")
    ap.add_argument("--topic", help="文档主题（配合 --add）")
    ap.add_argument("--source", help="来源需求（配合 --add）")
    ap.add_argument("--status", choices=STATUSES, default="active")
    ap.add_argument("--archive", metavar="文件", help="归档一份文档")
    ap.add_argument("--list", action="store_true", help="列出全部文档")
    ap.add_argument("--rebuild", action="store_true", help="重建 DOCS.md")
    ap.add_argument("--check", action="store_true", help="检查未登记/不合规/重复主题")
    ap.add_argument("--init", action="store_true", help="创建 docs/ 与空索引")
    args = ap.parse_args()

    root = find_project_root(args.project)
    docs_dir = root / AI_DEV_DIR / DOCS_DIR

    if args.init:
        return cmd_init(root)
    if args.list:
        docs = scan_docs(docs_dir)
        if not docs:
            print("[!] 还没有文档。")
            return 1
        for d in docs:
            flag = "  [不合规]" if d.malformed else ""
            print(f"  [{d.status or '?':<8}] docs/{d.name:<28} {d.topic}{flag}")
        return 0
    if args.find:
        return cmd_find(root, args.find)
    if args.add:
        if not args.topic or not args.source:
            sys.exit("[!] --add 需要同时给 --topic 与 --source。")
        return cmd_add(root, args.add, args.topic, args.source, args.status)
    if args.archive:
        return cmd_archive(root, args.archive)
    if args.check:
        return cmd_check(root)
    if args.rebuild:
        if not docs_dir.is_dir():
            print(f"[!] {docs_dir} 不存在，先 --init。", file=sys.stderr)
            return 1
        n, idx = rebuild_index(docs_dir)
        print(f"[+] 重建 {idx.relative_to(root)}（{n} 份文档）")
        return 0
    if args.intake:
        if args.user_file:
            user_text = (sys.stdin.read() if args.user_file == "-"
                         else read_text(Path(args.user_file)))
        elif args.user:
            user_text = args.user
        else:
            sys.exit("[!] --intake 需要 --user 或 --user-file 提供用户原话。")
        return intake(root, user_text)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
