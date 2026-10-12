#!/usr/bin/env python3
"""Health check for a project onboarded with devctx."""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import AI_DEV_DIR, DOCS_DIR, HANDOFFS_DIR, find_project_root, load_ignore_lines, read_text, write_text  # noqa: E402
import sync_rules  # noqa: E402
import build_index  # noqa: E402
import archive_handoff  # noqa: E402
import init_project  # noqa: E402
import log_handoff  # noqa: E402
import doc_registry  # noqa: E402

REQUIRED = [
    "START_HERE.md", "HANDOFF.md", "project-brief.md",
    "architecture.md", "conventions.md", "ignore.conf",
]
L2_DOCS = ["project-brief.md", "architecture.md", "conventions.md"]
OPTIONAL_DOCS = ["glossary.md", "lessons.md"]
HANDOFF_LIMIT = 120
FILLED_RATIO = 0.25
HANDOFFS_ENTRY_LIMIT = 200      # 超过就该归档旧条目
HANDOFFS_MALFORMED_LIMIT = 5    # 格式待修条目超过这个数视为腐化


@dataclass
class Finding:
    level: str
    area: str
    msg: str


def _edit_ratio(text: str, template: str) -> float:
    def norm(s: str) -> list[str]:
        s = s.replace("{{DATE}}", "x").replace("{{PROJECT_NAME}}", "x")
        return [ln.strip() for ln in s.splitlines() if ln.strip()]
    cur, tpl = norm(text), set(norm(template))
    if not cur:
        return 0.0
    changed = sum(1 for ln in cur if ln not in tpl)
    return changed / len(cur)


def check_files(ai: Path, fix: bool) -> list[Finding]:
    out = []
    missing = [name for name in REQUIRED if not (ai / name).exists()]
    for extra_dir in ("decisions", "archive"):
        if not (ai / extra_dir).exists():
            missing.append(extra_dir + "/")
    if missing:
        if fix:
            for src_name, dest_rel in init_project.COPY_MAP.items():
                dest = ai / dest_rel
                if not dest.exists():
                    src = init_project.TEMPLATES / src_name
                    if src.exists():
                        write_text(dest, read_text(src))
            (ai / "archive").mkdir(exist_ok=True)
            (ai / "decisions").mkdir(exist_ok=True)
            out.append(Finding("ok", "files", f"created missing: {', '.join(missing)}"))
        else:
            out.append(Finding("err", "files",
                               f"missing {', '.join(missing)}; rerun with --fix"))
    else:
        out.append(Finding("ok", "files", "all required context files present"))

    for name in L2_DOCS:
        p, tpl = ai / name, init_project.TEMPLATES / name
        if not p.exists() or not tpl.exists():
            continue
        ratio = _edit_ratio(read_text(p), read_text(tpl))
        if ratio < FILLED_RATIO:
            out.append(Finding("warn", "L2 docs",
                               f"{name} is still the shipped template "
                               f"(only {ratio:.0%} of lines edited); the AI must draft it from code"))
        else:
            out.append(Finding("ok", "L2 docs",
                               f"{name} drafted ({ratio:.0%} differs from template)"))
    for name in OPTIONAL_DOCS:
        p, tpl = ai / name, init_project.TEMPLATES / name
        if not p.exists():
            continue
        ratio = _edit_ratio(read_text(p), read_text(tpl)) if tpl.exists() else 1.0
        if ratio < FILLED_RATIO:
            out.append(Finding("warn", "L2 docs",
                               f"{name} (optional) is still the template "
                               f"(only {ratio:.0%} edited); draft it or delete it"))
        else:
            out.append(Finding("ok", "L2 docs",
                               f"{name} (optional) drafted ({ratio:.0%} differs from template)"))
    return out


def check_index(root: Path, ai: Path, fix: bool) -> list[Finding]:
    out = []
    idx = ai / "code-index.md"
    patterns = load_ignore_lines(root)
    _, records = build_index.walk_project(root, patterns, max_depth=6)
    newest, newest_name = 0.0, ""
    for rel, _style, _n, _d in records:
        m = (root / rel).stat().st_mtime
        if m > newest:
            newest, newest_name = m, rel
    if not idx.exists():
        if fix:
            subprocess.run([sys.executable, str(HERE / "build_index.py"), str(root)])
            out.append(Finding("ok", "code-index", "created code-index.md"))
        else:
            out.append(Finding("err", "code-index", "missing; rerun with --fix"))
        return out
    idx_mtime = idx.stat().st_mtime
    if newest > idx_mtime + 1:
        age = _dt.datetime.fromtimestamp(newest).strftime("%m-%d %H:%M")
        if fix:
            subprocess.run([sys.executable, str(HERE / "build_index.py"), str(root)])
            out.append(Finding("ok", "code-index",
                               f"rebuilt (newer source: {newest_name} @ {age})"))
        else:
            out.append(Finding("warn", "code-index",
                               f"stale: {newest_name} changed @ {age}, after the index; "
                               "rerun with --fix"))
    else:
        out.append(Finding("ok", "code-index", f"fresh ({len(records)} source files)"))
    return out


def check_handoff(ai: Path, root: Path, fix: bool) -> list[Finding]:
    h = ai / "HANDOFF.md"
    if not h.exists():
        return []
    n_lines = len(read_text(h).splitlines())
    if n_lines > HANDOFF_LIMIT:
        if fix:
            archive_handoff.archive(root, HANDOFF_LIMIT)
            return [Finding("ok", "HANDOFF", f"archived; was {n_lines} lines")]
        return [Finding("warn", "HANDOFF",
                        f"{n_lines} lines > {HANDOFF_LIMIT}; rerun with --fix to archive")]
    return [Finding("ok", "HANDOFF", f"{n_lines} lines within budget")]


def check_pointers(root: Path, fix: bool) -> list[Finding]:
    if fix:
        actions = sync_rules.sync(root, mode="detected")
        changed = [a for a in actions if a.status in ("written", "updated", "injected")]
        if changed:
            return [Finding("ok", "pointers",
                            "synced: " + ", ".join(a.rel for a in changed))]
        return [Finding("ok", "pointers", f"{len(actions)} pointer(s) already in sync")]
    actions = sync_rules.sync(root, mode="detected", check=True)
    bad = [a for a in actions if a.status in ("missing", "conflict")]
    if bad:
        detail = "; ".join(f"{a.rel}:{a.status}" for a in bad)
        return [Finding("warn", "pointers", f"{detail}; rerun with --fix")]
    return [Finding("ok", "pointers", f"{len(actions)} tool pointer(s) in sync")]


def check_git(root: Path) -> list[Finding]:
    gi = root / ".gitignore"
    if not gi.exists():
        return [Finding("ok", "git", "no .gitignore (project may not use git yet)")]
    text = read_text(gi).lstrip("\ufeff")
    if re.search(r"^\s*\.ai-dev\b", text, re.M):
        return [Finding("warn", "git",
                        ".ai-dev is git-ignored: shared context will NOT reach teammates/tools")]
    return [Finding("ok", "git", ".ai-dev is not ignored")]


def check_schema(ai: Path) -> list[Finding]:
    sh = ai / "START_HERE.md"
    if not sh.is_file():
        return []
    head = read_text(sh)[:500]
    if "devctx-schema:" in head:
        m = re.search(r"devctx-schema:\s*(\S+)", head)
        ver = m.group(1) if m else "unknown"
        return [Finding("ok", "schema", f"START_HERE schema version: {ver}")]
    return [Finding("warn", "schema",
                    "START_HERE.md has no devctx-schema marker; old project, rerun init with --force to update")]


def check_handoffs(root: Path, ai: Path, fix: bool) -> list[Finding]:
    """腐化检查：对话流水目录本身、索引一致性、条目格式、体积。"""
    out = []
    handoffs = ai / HANDOFFS_DIR
    if not handoffs.is_dir():
        if fix:
            log_handoff.cmd_init(root)
            return [Finding("ok", "handoffs", f"created {HANDOFFS_DIR}/ + empty index")]
        return [Finding("warn", "handoffs",
                        f"{HANDOFFS_DIR}/ missing; every turn should append a record — "
                        "rerun with --fix to create it")]

    entries = log_handoff.scan_entries(handoffs)
    idx = handoffs / log_handoff.INDEX_NAME

    if not entries:
        if fix:
            log_handoff.rebuild_index(handoffs)
            out.append(Finding("ok", "handoffs", f"{HANDOFFS_DIR}/ empty (no records yet)"))
        else:
            out.append(Finding("warn", "handoffs",
                               f"{HANDOFFS_DIR}/ has no records; compression never ran"))
        return out

    # 索引与磁盘一致性：数量不符就是漂移
    idx_count = None
    if idx.is_file():
        m = re.search(r"条目数：(\d+)", read_text(idx))
        idx_count = int(m.group(1)) if m else None
    if idx_count is None:
        if fix:
            n, _ = log_handoff.rebuild_index(handoffs)
            out.append(Finding("ok", "handoffs", f"index rebuilt ({n} records)"))
        else:
            out.append(Finding("warn", "handoffs",
                               f"{log_handoff.INDEX_NAME} unreadable or header missing; "
                               "rerun with --fix"))
    elif idx_count != len(entries):
        if fix:
            n, _ = log_handoff.rebuild_index(handoffs)
            out.append(Finding("ok", "handoffs",
                               f"index rebuilt: {idx_count} -> {n} records"))
        else:
            out.append(Finding("warn", "handoffs",
                               f"index says {idx_count} record(s) but {len(entries)} on disk "
                               "(drifted); rerun with --fix"))

    # 条目格式：缺 schema / 缺用户原话 / 无压缩节
    bad = [e for e in entries if e.malformed]
    if len(bad) > HANDOFFS_MALFORMED_LIMIT:
        detail = "; ".join(f"{e.name}:{','.join(e.malformed)}" for e in bad[:3])
        out.append(Finding("err", "handoffs",
                           f"{len(bad)} record(s) violate the compression spec "
                           f"(> {HANDOFFS_MALFORMED_LIMIT}); e.g. {detail}"))
    elif bad:
        detail = "; ".join(f"{e.name}:{','.join(e.malformed)}" for e in bad[:3])
        out.append(Finding("warn", "handoffs",
                           f"{len(bad)} record(s) incomplete; e.g. {detail}"))
    else:
        out.append(Finding("ok", "handoffs",
                           f"{len(entries)} record(s) all match the compression spec"))

    # 体积：条目太多该归档，否则新会话读索引越来越贵
    if len(entries) > HANDOFFS_ENTRY_LIMIT:
        if fix:
            moved = archive_handoff.archive_entries(root, HANDOFFS_ENTRY_LIMIT)
            out.append(Finding("ok", "handoffs",
                               f"archived {moved} old record(s) to archive/{HANDOFFS_DIR}/"))
        else:
            out.append(Finding("warn", "handoffs",
                               f"{len(entries)} records > {HANDOFFS_ENTRY_LIMIT}; "
                               "rerun with --fix to archive the oldest"))
    else:
        out.append(Finding("ok", "handoffs", f"{len(entries)} record(s) within budget"))

    # HANDOFF.md 不能落后于流水：否则"当前状态"和"每轮记录"对不上
    h = ai / "HANDOFF.md"
    if h.is_file() and idx.is_file():
        newest = max(e.path.stat().st_mtime for e in entries)
        if h.stat().st_mtime + 60 < newest:
            out.append(Finding("warn", "handoffs",
                               "HANDOFF.md is older than the newest record; "
                               "roll the latest record into HANDOFF.md"))
    return out


def check_docs(root: Path, ai: Path, fix: bool) -> list[Finding]:
    """腐化检查：文档是否登记在案、格式合规、无重复主题、来源可追溯。"""
    out = []
    docs_dir = ai / DOCS_DIR
    if not docs_dir.is_dir():
        if fix:
            doc_registry.cmd_init(root)
            return [Finding("ok", "docs", f"created {DOCS_DIR}/ + {doc_registry.DOC_INDEX_NAME}")]
        return [Finding("warn", "docs",
                        f"{DOCS_DIR}/ missing; 需求文档无处安放会导致乱建文件 — "
                        "rerun with --fix to create it")]

    docs = doc_registry.scan_docs(docs_dir)
    idx = docs_dir / doc_registry.DOC_INDEX_NAME

    if not docs:
        out.append(Finding("ok", "docs", f"{DOCS_DIR}/ empty (no requirement docs yet)"))
        return out

    on_disk = {d.name for d in docs}
    in_index: set[str] = set()
    if idx.is_file():
        in_index = set(re.findall(r"^\|\s*`([^`]+\.md)`", read_text(idx), re.M))
    orphans = sorted(on_disk - in_index)
    if orphans:
        if fix:
            for d in docs:
                d.links = doc_registry.count_links(root, d.name)
            doc_registry.rebuild_index(docs_dir)
            out.append(Finding("ok", "docs",
                               f"registered un-indexed doc(s): {', '.join(orphans)}"))
        else:
            out.append(Finding("err", "docs",
                               f"doc(s) exist but are not registered in "
                               f"{doc_registry.DOC_INDEX_NAME}: {', '.join(orphans)}; "
                               "rerun with --fix"))
    elif idx.is_file():
        out.append(Finding("ok", "docs", f"{len(docs)} doc(s) all registered"))
    else:
        out.append(Finding("warn", "docs",
                           f"{doc_registry.DOC_INDEX_NAME} missing; rerun with --fix"))

    bad = [d for d in docs if d.malformed]
    if bad:
        detail = "; ".join(f"{d.name}:{','.join(d.malformed)}" for d in bad[:3])
        out.append(Finding("warn", "docs", f"{len(bad)} doc(s) incomplete; e.g. {detail}"))

    dupes = []
    for i, a in enumerate(docs):
        for b in docs[i + 1:]:
            if a.is_archived or b.is_archived:
                continue
            if doc_registry._score(doc_registry._terms(a.topic),
                                   doc_registry._terms(b.topic)) >= 6.0:
                dupes.append(f"{a.name} vs {b.name}")
    if dupes:
        out.append(Finding("err", "docs",
                           f"duplicate topic(s): {'; '.join(dupes[:3])} — merge them, "
                           "one requirement maps to one document"))
    else:
        out.append(Finding("ok", "docs", "no duplicate topics"))

    unlinked = [d.name for d in docs
                if not d.is_archived and doc_registry.count_links(root, d.name) == 0]
    if unlinked:
        out.append(Finding("warn", "docs",
                           f"no handoff record references: {', '.join(unlinked[:3])}; "
                           "来源需求不可追溯，补一条带 Intent 的记录"))
    return out


def run_doctor(root: Path, fix: bool) -> list[Finding]:
    ai = root / AI_DEV_DIR
    if not ai.is_dir():
        return [Finding("err", "init",
                        f"{AI_DEV_DIR}/ not found under {root}; run init_project.py first")]
    findings: list[Finding] = []
    findings += check_files(ai, fix)
    findings += check_index(root, ai, fix)
    findings += check_handoff(ai, root, fix)
    findings += check_handoffs(root, ai, fix)
    findings += check_docs(root, ai, fix)
    findings += check_pointers(root, fix)
    findings += check_git(root)
    findings += check_schema(ai)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    root = find_project_root(args.project)
    print(f"devctx doctor — {root}\n")
    findings = run_doctor(root, args.fix)
    icon = {"ok": "[OK]", "warn": "[! ]", "err": "[XX]"}
    for f in findings:
        print(f"{icon[f.level]} {f.area.ljust(11)} {f.msg}")
    errs = [f for f in findings if f.level == "err"]
    warns = [f for f in findings if f.level == "warn"]
    print(f"\n{len(findings)} checks: {len(errs)} error(s), {len(warns)} warning(s)"
          + (" — --fix applied" if args.fix else ""))
    if errs:
        return 2
    if warns:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
