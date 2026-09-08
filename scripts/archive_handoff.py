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
from _common import AI_DEV_DIR, find_project_root, read_text, write_text  # noqa: E402

MARK = "ARCHIVE-LINE"
HIST_HEADING = "## 历史完成记录"


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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--max-lines", type=int, default=120)
    args = ap.parse_args()
    return archive(find_project_root(args.project), args.max_lines)


if __name__ == "__main__":
    sys.exit(main())
