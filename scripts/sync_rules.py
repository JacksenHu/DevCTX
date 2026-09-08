#!/usr/bin/env python3
"""Sync thin pointer files of every AI coding tool to the single source `.ai-dev/`.

Modes:
  detected (default): only tools whose evidence exists in the project, plus AGENTS.md
  all: write pointers for every known tool
  --check: report only, exit code 2 when anything is missing/drifted (CI friendly)
  --inject: when a hand-written rule file exists, insert an auto-managed block
            instead of skipping it (idempotent: existing block is replaced)
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    BEGIN_BLOCK,
    END_BLOCK,
    TOOL_SPECS,
    ToolSpec,
    find_project_root,
    injected_block,
    is_own_pointer,
    pointer_text,
    read_text,
    write_text,
)


@dataclass
class Action:
    tid: str
    rel: str
    status: str          # written | updated | injected | skipped | conflict | missing
    note: str = ""


def select_specs(root: Path, mode: str) -> list[ToolSpec]:
    if mode == "all":
        return list(TOOL_SPECS)
    chosen = []
    for spec in TOOL_SPECS:
        if spec.always or any((root / ev).exists() for ev in spec.evidence):
            chosen.append(spec)
    return chosen


def _replace_injected_block(text: str, block: str) -> str:
    pat = re.compile(
        rf"<!-- {re.escape(BEGIN_BLOCK)} -->.*?<!-- {re.escape(END_BLOCK)} -->\n?",
        re.DOTALL,
    )
    if pat.search(text):
        return pat.sub(block, text, count=1)
    base = text.rstrip("\n")
    return f"{base}\n\n{block}"


def sync_one(root: Path, spec: ToolSpec, inject: bool, check: bool) -> Action:
    target = root / spec.rel
    wanted = pointer_text(spec)

    if not target.exists():
        if check:
            return Action(spec.tid, spec.rel, "missing", spec.note)
        write_text(target, wanted)
        return Action(spec.tid, spec.rel, "written", spec.note)

    current = read_text(target)

    # 1) file already carries an injected block: refresh only inside the block,
    #    hand-written content outside the markers must be preserved
    if BEGIN_BLOCK in current:
        new_text = _replace_injected_block(current, injected_block(spec))
        if new_text.strip() == current.strip():
            return Action(spec.tid, spec.rel, "skipped", "injected block current")
        if check:
            return Action(spec.tid, spec.rel, "conflict", "injected block drifted")
        write_text(target, new_text)
        return Action(spec.tid, spec.rel, "injected", "block refreshed")

    # 2) the whole file is our standalone pointer (no injected block): safe to refresh
    if is_own_pointer(current):
        if current.strip() == wanted.strip():
            return Action(spec.tid, spec.rel, "skipped", "already in sync")
        if check:
            return Action(spec.tid, spec.rel, "conflict", "pointer drifted")
        write_text(target, wanted)
        return Action(spec.tid, spec.rel, "updated", "pointer refreshed")

    # 3) otherwise it is fully hand-written content
    if not inject:
        return Action(
            spec.tid, spec.rel, "conflict",
            f"hand-written {spec.rel} preserved; rerun with --inject to add block",
        )
    if check:
        return Action(spec.tid, spec.rel, "missing", "no injected block yet")
    write_text(target, _replace_injected_block(current, injected_block(spec)))
    return Action(spec.tid, spec.rel, "injected", "block added to existing file")


def sync(root: Path, mode: str = "detected", inject: bool = False, check: bool = False) -> list[Action]:
    actions = []
    for spec in select_specs(root, mode):
        actions.append(sync_one(root, spec, inject, check))
    return actions


def _print_report(actions: list[Action]) -> None:
    icon = {
        "written": "[+]", "updated": "[~]", "injected": "[>]",
        "skipped": "[=]", "conflict": "[!]", "missing": "[ ]",
    }
    width = max((len(a.rel) for a in actions), default=0)
    for a in actions:
        print(f"{icon.get(a.status, '[?]')} {a.rel.ljust(width)}  ({a.tid}) {a.note}")
    bad = [a for a in actions if a.status in ("conflict", "missing")]
    print(f"\n{len(actions)} tool(s) inspected, {len(bad)} need attention.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".", help="project directory (default: cwd)")
    ap.add_argument("--mode", choices=["detected", "all"], default="detected")
    ap.add_argument("--all", action="store_true", help="shortcut for --mode all")
    ap.add_argument("--inject", action="store_true", help="inject block into hand-written rule files")
    ap.add_argument("--check", action="store_true", help="report only; exit 2 on drift")
    args = ap.parse_args()

    root = find_project_root(args.project)
    mode = "all" if args.all else args.mode
    actions = sync(root, mode=mode, inject=args.inject, check=args.check)
    _print_report(actions)
    if args.check and any(a.status in ("conflict", "missing") for a in actions):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
