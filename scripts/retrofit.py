#!/usr/bin/env python3
"""Retrofit an existing multi-agent project onto the devctx protocol.

Greenfield init assumes there is no history. A real legacy project breaks that
assumption in three places:

1. **Several agents / sessions already built it.** Their work lives in git
   history and scattered docs, not in `handoffs/`. New agents then cannot tell
   who did what, and re-litigate settled decisions.
2. **Documents already exist** (README, docs/, ADRs, specs) written by nobody
   in particular. Dumping them aside loses knowledge; importing them blindly
   violates "one requirement, one document".
3. **Half-done work is invisible.** TODO/FIXME stubs and abandoned branches
   are exactly the "open Next" items the protocol runs on.

This script turns that history into protocol-shaped evidence:

    scan        # 证据清点：历史会话画像（git 挖掘）+ 存量文档清单 + 半成品信号
    plan        # 把证据填进接入 SOP，输出该项目的具体接入顺序
    baseline    # 为每个历史会话写一条"基线重建"记录（agent=retrofit），
                #   给它一个稳定的 DevCTX 会话 Id——这就是"压缩各会话 Id"
    consolidate # 存量文档归并建议；--apply 把已放进 docs/ 的文档登记进 DOCS.md

Division of labour: the script does the mechanical parts (mining, mapping,
registering, writing well-formed records). Folding the *content* of legacy
docs into one-requirement-one-document shape stays with the AI — the report
tells it exactly which doc merges into which.

Usage (called by the AI, never by the user)::

    python scripts/retrofit.py <根> scan [--max-commits 400]
    python scripts/retrofit.py <根> plan
    python scripts/retrofit.py <根> baseline --session legacy-cursor-1 \
        --topic auth-sms-login --user-file u.txt \
        --done "..." --next "- [ ] ..." --risks "..."
    python scripts/retrofit.py <根> consolidate [--apply]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    AI_DEV_DIR,
    DOCS_DIR,
    DOC_INDEX_NAME,
    find_project_root,
    load_ignore_lines,
    match_ignore,
    read_text,
)
import doc_registry  # noqa: E402
import log_handoff  # noqa: E402

RETROFIT_AGENT = "retrofit"
SESSION_GAP_DAYS = 14          # 同一人两次提交间隔超过它，视为两个历史会话
STUB_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
_H1_RE = re.compile(r"^#\s+(.+)$", re.M)
_US = "\x1f"


# ------------------------------------------------------------------ git mining

@dataclass
class SessionProfile:
    session: str                # 建议的 DevCTX 会话 Id，例 legacy-alice-1
    who: str                    # 作者名 <邮箱>
    first: str = ""             # YYYY-MM-DD
    last: str = ""
    commits: int = 0
    terms: list = field(default_factory=list)   # 提交主题高频词

    @property
    def span(self) -> str:
        return self.first if self.first == self.last else f"{self.first}~{self.last}"


def _slug(text: str, fallback: str = "agent") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:24] or fallback


def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _top_terms(texts: list, k: int = 5) -> list:
    freq: dict = {}
    for t in texts:
        for w in re.findall(r"[A-Za-z0-9_.\-]{3,}", t):
            freq[w.lower()] = freq.get(w.lower(), 0) + 1
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", t):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    freq[g] = freq.get(g, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:k]]


def mine_sessions(root: Path, max_commits: int = 400) -> tuple:
    """Group git history into per-agent, per-time-window session profiles.

    Returns (profiles, note). note explains why mining was skipped when it was.
    """
    out = _git(root, "rev-parse", "--is-inside-work-tree")
    if out is None or out.strip() != "true":
        return [], "不是 git 仓库（或 git 不可用）——历史会话只能由用户口述，见 plan 的兜底步骤"
    fmt = _US.join(["%H", "%an", "%ae", "%ad", "%s"])
    log = _git(root, "log", f"--pretty=format:{fmt}", "--date=short",
               "--no-merges", f"-n{max_commits}")
    if not log or not log.strip():
        return [], "git 历史为空——同样走口述兜底"

    rows = []
    for line in log.splitlines():
        parts = line.split(_US)
        if len(parts) == 5:
            rows.append(parts)
    # git log 是新到旧；按作者分组，组内按时间正序切窗口
    by_author: dict = {}
    for _h, an, ae, ad, subject in rows:
        by_author.setdefault(f"{an} <{ae}>", []).append((ad, subject))
    profiles: list = []
    for who, items in by_author.items():
        items.sort(key=lambda x: x[0])
        windows: list = []
        for ad, subject in items:
            prev_last = windows[-1][-1][0] if windows else None
            if prev_last is None or (_dt.date.fromisoformat(ad)
                                     - _dt.date.fromisoformat(prev_last)).days > SESSION_GAP_DAYS:
                windows.append([])
            windows[-1].append((ad, subject))
        base = _slug(who.split("<")[0].strip() or who.split("<")[-1].rstrip(">"))
        for i, w in enumerate(windows, 1):
            suffix = "" if len(windows) == 1 else f"-{i}"
            profiles.append(SessionProfile(
                session=f"legacy-{base}{suffix}",
                who=who,
                first=w[0][0], last=w[-1][0], commits=len(w),
                terms=_top_terms([s for _, s in w]),
            ))
    profiles.sort(key=lambda p: (p.first, p.session))
    note = ""
    if len(rows) >= max_commits:
        note = f"（仅分析最近 {max_commits} 条提交，--max-commits 可调）"
    return profiles, note


# ------------------------------------------------------------- legacy doc scan

@dataclass
class LegacyDoc:
    rel: str
    title: str
    lines: int
    excerpt: str
    terms_text: str = ""       # 参与主题匹配的正文（截断），标题匹配不到时靠它


def find_legacy_docs(root: Path) -> list:
    """Markdown outside .ai-dev/ — the raw material for consolidation."""
    patterns = load_ignore_lines(root)
    out: list = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        keep = []
        for d in dirnames:
            parts = tuple(rel_dir.parts) + (d,) if rel_dir.parts else (d,)
            if d == ".git" or match_ignore(parts, patterns):
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if not fn.lower().endswith(".md"):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root)
            parts = rel.parts
            if parts and parts[0] == AI_DEV_DIR:
                continue
            if match_ignore(parts, patterns):
                continue
            text = read_text(p)
            m = _H1_RE.search(text)
            title = m.group(1).strip() if m else fn[:-3]
            out.append(LegacyDoc(
                rel=rel.as_posix(), title=title,
                lines=len(text.splitlines()),
                excerpt=" ".join(text.split())[:80],
                # 归并匹配要看正文：标题叫「API 约定」的文档，主题在正文里
                terms_text=" ".join(text.split())[:2000],
            ))
    out.sort(key=lambda d: d.rel)
    return out


def scan_stubs(root: Path, patterns: list) -> dict:
    """TODO/FIXME counts per top-level directory — the half-done signals."""
    counts: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames
                       if d != ".git" and not match_ignore(
                           tuple(rel_dir.parts) + (d,) if rel_dir.parts else (d,),
                           patterns)]
        for fn in filenames:
            if not fn.lower().endswith((".py", ".js", ".ts", ".tsx", ".jsx",
                                        ".go", ".rs", ".java", ".md")):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root)
            if match_ignore(rel.parts, patterns):
                continue
            try:
                text = read_text(p)
            except OSError:
                continue
            n = len(STUB_RE.findall(text))
            if n:
                top = rel.parts[0] if len(rel.parts) > 1 else "（根目录）"
                counts[top] = counts.get(top, 0) + n
    return dict(sorted(counts.items(), key=lambda x: -x[1])[:8])


# --------------------------------------------------------------------- output

def cmd_scan(root: Path, max_commits: int) -> int:
    profiles, note = mine_sessions(root, max_commits)
    docs = find_legacy_docs(root)
    stubs = scan_stubs(root, load_ignore_lines(root))

    print(f"存量项目证据清点 — {root}\n")
    print(f"一、历史会话画像（{len(profiles)} 个）{note}")
    if profiles:
        for p in profiles:
            terms = "、".join(p.terms[:4]) or "（主题词不足）"
            print(f"  {p.session:<24} {p.who:<28} {p.span} "
                  f"{p.commits} 次提交 · 高频词：{terms}")
        print("\n  → 每个画像对应一个历史会话；接入时用 baseline 为它写一条记录，")
        print("    会话 Id 就用上面的建议值（可改），从此新 Agent 读 INDEX 即知谁做过什么。")
    else:
        print("  （无可挖掘的 git 历史）")
        print("  → 兜底：请用户口述'用过哪些工具/会话、各自在做什么、做到哪了」，")
        print("    每段口述对应一个 baseline 记录。")

    print(f"\n二、存量文档（{len(docs)} 份 .md，不含 .ai-dev/）")
    for d in docs:
        print(f"  {d.rel:<40} {d.lines:>5} 行  {d.title}")
    if docs:
        print("\n  → consolidate 会给出'并入哪份需求文档 / 独立成篇'的建议；")
        print("    内容合并由 AI 按建议完成，脚本只负责登记与查重。")

    print(f"\n三、半成品信号（TODO/FIXME/XXX/HACK 计数，前 8）")
    if stubs:
        for k, v in stubs.items():
            print(f"  {k:<24} {v} 处")
        print("\n  → 这些就是 handoffs 里未来的「开放 Next」候选，按主题分拣进各需求文档的「待办」。")
    else:
        print("  （未发现）")
    return 0


def cmd_plan(root: Path) -> int:
    profiles, note = mine_sessions(root)
    docs = find_legacy_docs(root)
    stubs = scan_stubs(root, load_ignore_lines(root))
    has_ai = (root / AI_DEV_DIR).is_dir()

    print(f"存量项目接入顺序 — {root}\n")
    steps = [
        ("init", f"python scripts/init_project.py <根> [--all]"
                 + ("" if has_ai else "；已存在 .ai-dev/ 则跳过本步")
                 + "。大项目先补 .ai-dev/ignore.conf（vendor/node_modules/构建产物），"
                   "code-index 用 --max-lines/--max-depth 压规模，别让索引变成第二个仓库。"),
        ("spec 化现状", "以 code-index 为地图扫真实代码，亲自起草 L2 文档；"
                       "TODO/FIXME 与半成品按主题写进 specs/（已完成的标'已完成'，未完成的跑 spec→plan→tasks）。"),
        ("会话回填", f"为 {len(profiles)} 个历史会话画像逐个跑 "
                   "retrofit.py baseline --session <建议Id> --topic <主题> --user-file <原话> "
                   "--done/--next/--risks …（agent 固定 retrofit）。"
                   "用户手上有真实会话记录/Id 的可精确回填；没有就按画像名重建或让其口述，"
                   "记不得标'历史口述'，禁止编造。"
                   "没有这一步，新 Agent 不知道这些代码是谁在什么约束下写的。"),
        ("文档归并", f"对 {len(docs)} 份存量文档跑 retrofit.py consolidate 看建议，"
                   "按'一个需求一份文档'合并进 .ai-dev/docs/，再 --apply 登记；"
                   "原文不要复制两份，合并后在原处留路径引用。"),
        ("指针", "sync_rules.py 检测在用工具；已有手写规则用 --inject 注入标记块，块外内容绝不丢。"),
        ("体检", "doctor.py --fix 确认无 error；未登记文档、主题重复、索引漂移此时都应清零。"),
        ("交付", "一句话告知用户：已接入，历史会话与文档已回填，以后每轮自动压缩、动手前自动受理。"),
    ]
    for i, (name, detail) in enumerate(steps, 1):
        print(f"{i}. {name}：{detail}")
    print("\n主动项（问一句，不索要作业）：若用户有各工具的历史会话记录或真实会话 Id")
    print("（Claude Code ~/.claude/projects/、Cursor 工作区存储等），要过来可把对应画像")
    print("升级为精确回填；没有也不阻塞——git 画像名本身就是稳定标签，口述同样有效。")
    if stubs:
        print(f"\n提醒：扫描到 {sum(stubs.values())} 处半成品信号（前 8 个目录见 scan），"
              "它们是接入后第一批「开放 Next」的来源。")
    if not has_ai:
        print("\n注意：本项目还没有 .ai-dev/，第 1 步会创建；若用户只要文档整理不要全套接入，")
        print("      至少完成第 3、4 步——会话 Id 与文档归才是'多 Agent 遗留项目'的核心损失。")
    return 0


def cmd_baseline(root: Path, session: str, topic: str, user_text: str,
                 done: str | None, nxt: str | None, verify: str | None,
                 risks: str | None) -> int:
    """Write one reconstruction record for a historical session."""
    intent = f"RETROFIT → docs/{topic}.md（历史会话基线重建）"
    try:
        body = log_handoff.build_body(done, nxt, verify, risks, intent,
                                      None, None, False)
    except SystemExit:
        sys.exit("[!] 基线记录至少需要 --done/--next/--verify/--risks 之一；"
                 "这是'这个会话留下了什么'的压缩，不是自由散文。")
    if not user_text.strip():
        sys.exit("[!] --user/--user-file 必填：写用户当时给这个会话的原始指令"
                 "（记不得就写'历史口述：<谁在什么时候让做什么>'，不要编造）。")
    path, sid = log_handoff.write_record(root, RETROFIT_AGENT, session,
                                         user_text, body, None)
    print(f"[+] 基线记录 {path.relative_to(root)}（会话 {sid} · agent={RETROFIT_AGENT}）")
    print("    INDEX.md 已重建——该历史会话从此可被任何新 Agent 检索到。")
    return 0


def cmd_consolidate(root: Path, apply_: bool) -> int:
    docs_dir = root / AI_DEV_DIR / DOCS_DIR
    registered = {d.name: d for d in doc_registry.scan_docs(docs_dir)}
    legacy = find_legacy_docs(root)

    # "未登记"的准确定义：在 docs/ 里但不在 DOCS.md 索引里（与 doctor 的检查一致），
    # 不是"扫不到"——scan_docs 本来就会扫到全部磁盘文件。
    idx = docs_dir / DOC_INDEX_NAME
    in_index: set = set()
    if idx.is_file():
        in_index = set(re.findall(r"^\|\s*`([^`]+\.md)`", read_text(idx), re.M))

    print(f"存量文档归并建议 — {root}\n")
    todo_merge, todo_new = [], []
    for d in legacy:
        terms = doc_registry._terms(d.title + " " + d.rel + " " + d.terms_text)
        best, best_s = None, 0.0
        for name, doc in registered.items():
            if doc.is_archived:
                continue
            s = doc_registry._score(terms, doc_registry._terms(doc.topic + " " + name))
            if s > best_s:
                best, best_s = (name, doc), s
        if best and best_s >= 6.0:
            todo_merge.append((d, best[1].name, best[1].topic, best_s))
        else:
            todo_new.append(d)

    if todo_merge:
        print("建议并入现有需求文档（内容合并由 AI 完成，合并在原处留路径引用）：")
        for d, name, topic, s in todo_merge:
            print(f"  {d.rel:<40} → docs/{name}（「{topic}」· 相关度 {s:.1f}）")
    if todo_new:
        print("\n建议独立成篇（AI 提炼成'一个需求一份文档'后放入 .ai-dev/docs/ 并登记）：")
        for d in todo_new:
            print(f"  {d.rel:<40} 「{d.title}」{d.lines} 行")
    if not todo_merge and not todo_new:
        print("（没有发现 .ai-dev/ 之外的存量文档）")

    unregistered = [p for p in sorted(docs_dir.glob("*.md"))
                    if p.name not in in_index
                    and p.name != DOC_INDEX_NAME
                    and not p.name.startswith("0000-")]
    if unregistered:
        print(f"\n.ai-dev/docs/ 里有 {len(unregistered)} 份未登记文档：")
        for p in unregistered:
            print(f"  {p.name}")
    else:
        print("\n.ai-dev/docs/ 内文档全部已登记。")

    if not apply_:
        print("\n（dry-run）AI 按上面建议完成合并后，加 --apply 登记 docs/ 里的文档。")
        return 0

    if not unregistered:
        print("\n无可登记项。")
        return 0
    n = 0
    for p in unregistered:
        d = doc_registry.parse_doc(p)
        topic = d.topic or p.stem
        source = f"RETROFIT {_dt.date.today().isoformat()}（存量文档归并登记）"
        rc = doc_registry.cmd_add(root, f"{AI_DEV_DIR}/{DOCS_DIR}/{p.name}",
                                  topic, source, "active")
        n += 1 if rc == 0 else 0
    print(f"\n[+] 已登记 {n} 份文档；跑 doc_registry.py --check 复核。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", default=".", help="项目目录（默认当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan", help="证据清点：历史会话画像 + 存量文档 + 半成品信号")
    p_scan.add_argument("--max-commits", type=int, default=400)
    sub.add_parser("plan", help="把证据填进接入 SOP，输出具体接入顺序")
    p_base = sub.add_parser("baseline", help="为一个历史会话写基线重建记录")
    p_base.add_argument("--session", required=True, help="该历史会话的 DevCTX 会话 Id")
    p_base.add_argument("--topic", required=True, help="归并目标需求文档主题（文件名用）")
    p_base.add_argument("--user", help="当时给这个会话的原始指令")
    p_base.add_argument("--user-file", help="同上，走文件（一字不删）")
    p_base.add_argument("--done")
    p_base.add_argument("--next")
    p_base.add_argument("--verify")
    p_base.add_argument("--risks")
    p_cons = sub.add_parser("consolidate", help="存量文档归并建议 / --apply 登记")
    p_cons.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = find_project_root(args.project)
    if args.cmd == "scan":
        return cmd_scan(root, args.max_commits)
    if args.cmd == "plan":
        return cmd_plan(root)
    if args.cmd == "baseline":
        if args.user_file:
            user_text = read_text(Path(args.user_file))
        elif args.user:
            user_text = args.user
        else:
            sys.exit("[!] baseline 需要 --user 或 --user-file。")
        return cmd_baseline(root, args.session, args.topic, user_text,
                            args.done, args.next, args.verify, args.risks)
    if args.cmd == "consolidate":
        return cmd_consolidate(root, args.apply)
    return 1


if __name__ == "__main__":
    sys.exit(main())
