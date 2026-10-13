#!/usr/bin/env python3
"""Archive the history section of `.ai-dev/HANDOFF.md` when it grows past budget.

Only content BELOW the `<!-- ARCHIVE-LINE -->` marker is ever moved; the active
state above the marker is never touched. If the file is over budget but the
history section is empty, it is left unchanged and the AI is told to compress
the active section itself.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    AI_DEV_DIR,
    HANDOFFS_DIR,
    ensure_stdio_safe,
    find_project_root,
    read_text,
    write_text,
)
import log_handoff  # noqa: E402

MARK = "ARCHIVE-LINE"
HIST_HEADING = "## 历史完成记录"


def archive_entries(root: Path, keep: int = 200) -> int:
    """Move the oldest per-conversation records into archive/handoffs/.

    Only records BEYOND the newest `keep` are ever moved — the active window is
    never touched, mirroring the ARCHIVE-LINE rule for HANDOFF.md. Returns the
    number of records moved.
    """
    handoffs = root / AI_DEV_DIR / HANDOFFS_DIR
    entries = log_handoff.scan_entries(handoffs)
    if len(entries) <= keep:
        return 0
    stale, active = entries[:-keep], entries[-keep:]

    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = root / AI_DEV_DIR / "archive" / HANDOFFS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"handoffs-{ts}.md"

    parts = [
        f"# 对话压缩记录归档 {ts}",
        "",
        f"> 由 archive_handoff.py 从 {HANDOFFS_DIR}/ 自动搬移（保留最近 {keep} 条），仅作留痕。",
        "",
    ]
    for e in stale:
        parts.append(f"## {e.name}")
        parts.append("")
        parts.append(read_text(e.path).rstrip())
        parts.append("")
    write_text(dest, "\n".join(parts).rstrip() + "\n")

    for e in stale:
        try:
            e.path.unlink()
        except OSError:
            pass

    log_handoff.rebuild_index(handoffs)
    print(f"[+] {len(stale)} record(s) -> {AI_DEV_DIR}/archive/{HANDOFFS_DIR}/{dest.name}; "
          f"{len(active)} active record(s) kept.")
    return len(stale)


def archive(root: Path, max_lines: int) -> int:
    handoff = root / AI_DEV_DIR / "HANDOFF.md"
    if not handoff.is_file():
        print(f"[!] {handoff} not found.", file=sys.stderr)
        return 1
    lines = read_text(handoff).splitlines()

    if len(lines) <= max_lines:
        print(f"[=] HANDOFF.md is {len(lines)} lines (<= {max_lines}); nothing to archive.")
        return 0

    mark_idx = next((i for i, ln in enumerate(lines) if MARK in ln), None)
    if mark_idx is None:
        print("[!] ARCHIVE-LINE marker missing; refusing to edit without a safety line.",
              file=sys.stderr)
        return 1
    hist_idx = next(
        (i for i in range(mark_idx, len(lines)) if lines[i].startswith(HIST_HEADING)), None
    )
    if hist_idx is None:
        print("[!] history heading missing below marker; refusing to edit.", file=sys.stderr)
        return 1

    body = [ln for ln in lines[hist_idx + 1:] if ln.strip()
            and not ln.strip().startswith("- 最近归档")]
    if not body:
        print(
            f"[!] HANDOFF.md is {len(lines)} lines but the archived (history) section is "
            "empty. Compress the ACTIVE sections above the marker yourself; do not delete "
            "active state by script."
        )
        return 0

    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_dir = root / AI_DEV_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    fname = f"handoff-{ts}.md"
    archived = (
        f"# HANDOFF 历史归档 {ts}\n\n"
        "> 由 archive_handoff.py 从 HANDOFF.md 自动搬移，仅作留痕。\n\n"
        + "\n".join(body).strip()
        + "\n"
    )
    write_text(archive_dir / fname, archived)

    new_lines = lines[: hist_idx + 1] + [
        "",
        f"- 最近归档：`archive/{fname}`（{ts}）",
        "",
    ]
    write_text(handoff, "\n".join(new_lines) + "\n")
    print(f"[+] {len(body)} history line(s) -> {AI_DEV_DIR}/archive/{fname}; "
          f"HANDOFF.md now {len(new_lines)} lines.")
    return 0


def main() -> int:
    ensure_stdio_safe()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--max-lines", type=int, default=120,
                    help="HANDOFF.md 行数上限（活跃区超限只提示，不自动删）")
    ap.add_argument("--max-entries", type=int, default=200, metavar="N",
                    help=f"{HANDOFFS_DIR}/ 记录数上限，超出则把最旧的搬进 archive/")
    ap.add_argument("--entries-only", action="store_true",
                    help="只归档对话流水记录，不碰 HANDOFF.md")
    args = ap.parse_args()
    root = find_project_root(args.project)
    if args.entries_only:
        moved = archive_entries(root, args.max_entries)
        if not moved:
            print(f"[=] {HANDOFFS_DIR}/ 记录数在 {args.max_entries} 以内，无需归档。")
        return 0
    rc = archive(root, args.max_lines)
    if rc != 0:
        return rc
    archive_entries(root, args.max_entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
