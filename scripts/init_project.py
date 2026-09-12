#!/usr/bin/env python3
"""Initialize the unified `.ai-dev/` context folder inside a target project.

Copies bundled templates (never overwriting existing content), builds the first
code index, and syncs pointer files for detected AI coding tools.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import AI_DEV_DIR, find_project_root, read_text, write_text  # noqa: E402
import sync_rules  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_ROOT / "assets" / "templates"

# template file -> destination relative to .ai-dev/
COPY_MAP = {
    "START_HERE.md": "START_HERE.md",
    "HANDOFF.md": "HANDOFF.md",
    "project-brief.md": "project-brief.md",
    "architecture.md": "architecture.md",
    "conventions.md": "conventions.md",
    "ignore.conf": "ignore.conf",
    "glossary.md": "glossary.md",
    "lessons.md": "lessons.md",
    "ADR-template.md": "decisions/0000-template.md",
}


def init(root: Path, mode: str, force: bool) -> int:
    ai = root / AI_DEV_DIR
    if (ai / "START_HERE.md").exists() and not force:
        print(f"[!] {AI_DEV_DIR}/START_HERE.md already exists in {root}.")
        print("    Re-run with --force to top up missing files (existing files are kept).")
        return 1
    if not TEMPLATES.is_dir():
        print(f"[!] templates missing: {TEMPLATES}", file=sys.stderr)
        return 1

    ai.mkdir(parents=True, exist_ok=True)
    (ai / "archive").mkdir(exist_ok=True)
    (ai / "decisions").mkdir(exist_ok=True)

    today = _dt.date.today().isoformat()
    for src_name, dest_rel in COPY_MAP.items():
        src = TEMPLATES / src_name
        dest = ai / dest_rel
        if dest.exists():
            print(f"[=] kept existing {AI_DEV_DIR}/{dest_rel}")
            continue
        text = read_text(src).replace("{{DATE}}", today).replace(
            "{{PROJECT_NAME}}", root.name
        )
        write_text(dest, text)
        print(f"[+] created {AI_DEV_DIR}/{dest_rel}")

    # first code index (non-fatal if it fails)
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "build_index.py"), str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(r.stdout.strip() or "(code index skipped)")
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)

    # pointer files for detected/all tools
    print("\n-- syncing tool pointer files --")
    actions = sync_rules.sync(root, mode=mode)
    sync_rules._print_report(actions)

    print(
        "\n[AI 必做，不要把空模板留给用户]\n"
        f"1. 立即扫描代码（以 code-index.md 为地图），自动起草 {AI_DEV_DIR}/project-brief.md、"
        "architecture.md、conventions.md、glossary.md（术语表，只留会被猜错的业务黑话/缩写）；"
        "lessons.md 初始可空，后续 HANDOFF 陷阱区某坑重复出现第二次再提升进来。"
        "事实只来自代码与用户，不确定标“待确认”，禁止编造。\n"
        "2. 用 architecture.md 写清模块分区与“改什么去哪改”速查。\n"
        "3. 初始化 HANDOFF.md 当前状态；然后用一句话告诉用户“已接入，以后直接提需求即可”。\n"
        "4. 用户无需手动运行任何脚本；其他 IDE 新开会话即由指针自动引导到 START_HERE.md。"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".", help="target project directory")
    ap.add_argument("--mode", choices=["detected", "all"], default="detected")
    ap.add_argument("--all", action="store_true", help="shortcut for --mode all")
    ap.add_argument("--force", action="store_true", help="top up missing files, keep existing")
    args = ap.parse_args()
    root = find_project_root(args.project)
    print(f"Project root: {root}\n")
    return init(root, "all" if args.all else args.mode, args.force)


if __name__ == "__main__":
    sys.exit(main())
