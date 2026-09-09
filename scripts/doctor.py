#!/usr/bin/env python3
"""Health check for a project onboarded with cross-ide-dev-context.

Checks: required context files present, L2 docs actually drafted (not blank
templates), code-index fresh vs source mtimes, HANDOFF within size budget,
tool pointers in sync, .ai-dev not accidentally git-ignored.

Usage:
  doctor.py [project]            report only; exit 0=healthy 1=warnings 2=errors
  doctor.py [project] --fix      auto-fix what is safely fixable
"""

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
from _common import AI_DEV_DIR, find_project_root, load_ignore_lines, read_text, write_text  # noqa: E402
import sync_rules  # noqa: E402
import build_index  # noqa: E402
import archive_handoff  # noqa: E402
import init_project  # noqa: E402

REQUIRED = [
    "START_HERE.md", "HANDOFF.md", "project-brief.md",
    "architecture.md", "conventions.md", "ignore.conf",
]
L2_DOCS = ["project-brief.md", "architecture.md", "conventions.md"]
OPTIONAL_DOCS = ["glossary.md"]  # present-only: absence is fine, blank template warns
HANDOFF_LIMIT = 120
FILLED_RATIO = 0.25  # <25% of lines differ from shipped template => still a template


@dataclass
class Finding:
    level: str          # ok | warn | err
    area: str
    msg: str


def _edit_ratio(text: str, template: str) -> float:
    """Share of current lines that differ from the shipped template (0=unchanged)."""
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
            continue  # optional: absence is not a finding
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
    actions = sync_rules.sync(root, mode="detected", check=True)  # read-only
    bad = [a for a in actions if a.status in ("missing", "conflict")]
    if bad:
        detail = "; ".join(f"{a.rel}:{a.status}" for a in bad)
        return [Finding("warn", "pointers", f"{detail}; rerun with --fix")]
    return [Finding("ok", "pointers", f"{len(actions)} tool pointer(s) in sync")]


def check_git(root: Path) -> list[Finding]:
    gi = root / ".gitignore"
    if not gi.exists():
        return [Finding("ok", "git", "no .gitignore (project may not use git yet)")]
    text = read_text(gi).lstrip("﻿")  # tolerate UTF-8 BOM (common on Windows)
    if re.search(r"^\s*\.ai-dev\b", text, re.M):
        return [Finding("warn", "git",
                        ".ai-dev is git-ignored: shared context will NOT reach teammates/tools")]
    return [Finding("ok", "git", ".ai-dev is not ignored")]


def run_doctor(root: Path, fix: bool) -> list[Finding]:
    ai = root / AI_DEV_DIR
    if not ai.is_dir():
        return [Finding("err", "init",
                        f"{AI_DEV_DIR}/ not found under {root}; run init_project.py first")]
    findings: list[Finding] = []
    findings += check_files(ai, fix)
    findings += check_index(root, ai, fix)
    findings += check_handoff(ai, root, fix)
    findings += check_pointers(root, fix)
    findings += check_git(root)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    root = find_project_root(args.project)
    print(f"cross-ide-dev-context doctor — {root}\n")
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
