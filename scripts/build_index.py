#!/usr/bin/env python3
"""Rebuild `.ai-dev/code-index.md`: a compact, auto-generated map of the codebase.

An AI tool reads this one file instead of walking/reading the whole repository,
which is the main token saver when switching IDEs/models. Respects .gitignore
and .ai-dev/ignore.conf. Output budget is capped (--max-lines).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import AI_DEV_DIR, find_project_root, load_ignore_lines, match_ignore  # noqa: E402

HASH_EXT = {
    ".py", ".rb", ".sh", ".bash", ".zsh", ".ps1", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".r", ".pl", ".tcl", ".gradle", ".properties", ".tf", ".proto",
}
SLASH_EXT = {
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".cc",
    ".cxx", ".hpp", ".cs", ".go", ".rs", ".swift", ".kt", ".kts", ".php", ".scala",
    ".dart", ".groovy", ".vue", ".svelte", ".m", ".mm",
}
DASH_EXT = {".sql", ".lua"}
HTML_EXT = {".html", ".htm", ".xml", ".svg"}
SEMI_EXT = {".clj", ".cljs", ".lisp", ".el"}
MD_EXT = {".md", ".mdx", ".rst"}
SPECIAL_NAMES = {"dockerfile", "makefile", "rakefile", "gemfile", ".env.example"}
SOURCE_EXT = (
    HASH_EXT | SLASH_EXT | DASH_EXT | HTML_EXT | SEMI_EXT | MD_EXT
    | {".json", ".json5", ".css", ".scss", ".sass", ".less"}
)
HEADER_READ = 4096
DESC_LIMIT = 120
TREE_NODE_CAP = 400


def comment_style(name: str) -> str | None:
    n = name.lower()
    if n in SPECIAL_NAMES or n.startswith("dockerfile"):
        return "hash"
    ext = Path(n).suffix
    if ext in HASH_EXT:
        return "py-doc" if ext == ".py" else "hash"
    if ext in SLASH_EXT:
        return "slash"
    if ext in DASH_EXT:
        return "dash"
    if ext in HTML_EXT:
        return "html"
    if ext in SEMI_EXT:
        return "semi"
    if ext in MD_EXT:
        return "md"
    return None


def _clean(line: str) -> str:
    s = line.strip().strip("* #-/>=\t").strip()
    for tag in ("@fileoverview ", "@file ", "@description "):
        if s.lower().startswith(tag):
            s = s[len(tag):].strip()
    return s


def extract_description(path: Path, style: str) -> str:
    try:
        with open(path, "rb") as f:
            raw = f.read(HEADER_READ)
    except OSError:
        return ""
    # utf-8-sig also strips a BOM if present (PowerShell/Notepad-saved files)
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    if style == "py-doc":
        i = 0
        while i < len(lines) and (not lines[i].strip() or lines[i].startswith("#!")):
            i += 1
        if i < len(lines) and lines[i].lstrip().startswith(('"""', "'''")):
            stripped = lines[i].lstrip()
            q = stripped[:3]
            rest = stripped[3:]
            if rest.rstrip().endswith(q):  # single-line docstring """..."""
                one = rest.rstrip()[: -len(q)].strip()
                if one:
                    return one[:DESC_LIMIT]
            first = rest.strip()
            if first:
                return first[:DESC_LIMIT]
            j = i + 1
            while j < len(lines):
                cand = _clean(lines[j])
                if cand:
                    return cand[:DESC_LIMIT]
                j += 1

    prefix = {"hash": "#", "slash": "//", "dash": "--", "semi": ";;"}.get(style)
    if style == "html":
        started = False
        buf: list[str] = []
        for line in lines[:20]:
            s = line.strip()
            if not started:
                if s.startswith("<!--"):
                    started = True
                    frag = s.replace("<!--", "", 1)
                    if "-->" in frag:
                        frag = frag.split("-->", 1)[0]
                    if _clean(frag):
                        return _clean(frag)[:DESC_LIMIT]
                    buf.append(frag)
                elif s and not s.startswith("<"):
                    # real text/code before any comment: stop; HTML/XML head
                    # tags (<!DOCTYPE>, <html>, <meta>) are skipped, not blockers
                    break
            else:
                if "-->" in s:
                    buf.append(s.split("-->", 1)[0])
                    break
                buf.append(s)
        return _clean(" ".join(buf))[:DESC_LIMIT]

    if style == "md":
        for line in lines[:10]:
            if line.lstrip().startswith("#"):
                return _clean(line.lstrip()[1:])[:DESC_LIMIT]
        return ""

    if prefix is None:
        return ""

    collected: list[str] = []
    started = False
    for line in lines[:20]:
        s = line.strip()
        if not s or s.startswith("#!"):
            if started:
                break
            continue
        if s.startswith(prefix):
            started = True
            cand = _clean(s[len(prefix):])
            if cand:
                collected.append(cand)
        elif s.startswith("/*"):  # block comment opener for slash-style
            frag = s[2:]
            if "*/" in frag:
                frag = frag.split("*/", 1)[0]
            if _clean(frag):
                return _clean(frag)[:DESC_LIMIT]
            collected.append(frag)
        elif started:
            break
        else:
            break
    return _clean(" ".join(collected))[:DESC_LIMIT]


def count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def walk_project(root: Path, patterns: list[str], max_depth: int):
    """Return (tree_lines, file_records). file_records: (rel_posix, style, lines, desc)."""
    tree_lines: list[str] = []
    records = []
    node_count = 0

    def rel_parts(dirpath: Path) -> tuple[str, ...]:
        return dirpath.relative_to(root).parts if dirpath != root else ()

    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        parts = rel_parts(dp)
        depth = len(parts)
        # prune ignored directories in-place
        kept_dirs = []
        for d in sorted(dirnames, key=str.lower):
            child_parts = parts + (d,)
            if not match_ignore(child_parts, patterns):
                kept_dirs.append(d)
        dirnames[:] = kept_dirs

        if depth <= max_depth and node_count < TREE_NODE_CAP:
            for d in dirnames:
                indent = "  " * depth
                tree_lines.append(f"{indent}{d}/")
                node_count += 1
                if node_count >= TREE_NODE_CAP:
                    tree_lines.append(f"{indent}... (tree truncated)")
                    break

        for fn in sorted(filenames, key=str.lower):
            child_parts = parts + (fn,)
            if match_ignore(child_parts, patterns):
                continue
            style = comment_style(fn)
            fpath = dp / fn
            if style is None:
                continue
            if fpath.stat().st_size > 512 * 1024:
                desc = "(large file, description skipped)"
            else:
                desc = extract_description(fpath, style)
            records.append(("/".join(child_parts), style, count_lines(fpath), desc))

    return tree_lines, records


def render(root: Path, tree_lines: list[str], records: list, max_lines: int) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    total_lines = sum(r[2] for r in records)
    out: list[str] = []
    out.append("# Code Index — 代码地图（自动生成，请勿手改）")
    out.append("")
    out.append(f"- 生成时间：{now}")
    out.append(f"- 源码文件：{len(records)} 个，合计约 {total_lines} 行")
    out.append("- 用法：先在本文件定位目标，再精读对应源码；不要全仓遍历。增删/移动文件后用 build_index.py 重建。")
    out.append("")
    out.append("## 目录结构")
    out.append("")
    out.append("```text")
    out.append(f"{root.name}/")
    budget = max_lines - 40
    out.extend(tree_lines)
    out.append("```")
    out.append("")
    out.append("## 文件清单（按目录分组）")
    out.append("")

    current_group = None
    truncated = 0
    for rel, _style, nlines, desc in records:
        if len(out) >= budget:
            truncated += 1
            continue
        group = str(Path(rel).parent).replace("\\", "/")
        if group != current_group:
            current_group = group
            out.append(f"### {group if group != '.' else '(root)'}")
            out.append("")
        suffix = f" — {desc}" if desc else ""
        out.append(f"- `{Path(rel).name}` ({nlines} 行){suffix}  <!-- {rel} -->")
    out.append("")
    if truncated:
        out.append(f"> 已达输出预算，另有 {truncated} 个文件未列出；用 --max-lines 扩大预算后重建。")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--max-lines", type=int, default=1200)
    ap.add_argument("--max-depth", type=int, default=4)
    args = ap.parse_args()

    root = find_project_root(args.project)
    ai = root / AI_DEV_DIR
    if not ai.is_dir():
        print(f"[!] {AI_DEV_DIR}/ not found under {root}; run init_project.py first.", file=sys.stderr)
        return 1

    patterns = load_ignore_lines(root)
    tree_lines, records = walk_project(root, patterns, args.max_depth)
    text = render(root, tree_lines, records, args.max_lines)
    out_path = ai / "code-index.md"
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"[+] wrote {out_path.relative_to(root)}: {len(records)} source files indexed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
