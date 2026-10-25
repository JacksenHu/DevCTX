#!/usr/bin/env python3
"""devctx 收工清理：按规格列出 AI 干活产生的缓存/临时/测试残留文件。

用户在收工（或每轮对话结束）时，除写压缩记录外，还应顺手处理 AI 干活
产生的无用文件（__pycache__、.pytest_cache、*.tmp、编辑器备份等）。
本脚本按一份**明文规格**扫描候选，默认只列不删（dry-run），由 AI 把清单
展示给用户、征得同意后再 --apply 删除。规格可 --list-specs 随时查看。

用法:
  cleanup.py [project]                       # 只扫描列出（安全，不删任何东西）
  cleanup.py [project] --apply [--yes]       # 按规格删除缓存/临时类（默认二次确认）
  cleanup.py [project] --apply --include-caution   # 连谨慎类一起删（体积大/重建慢）
  cleanup.py [project] --context N           # 顺带读最近 N 轮 handoff 记录（测试线索）
  cleanup.py [project] --list-specs         # 打印当前清理规格

安全边界（不可越过）:
  - 永不进入/删除: .git、.ai-dev、specs/、.specify/、node_modules、venv 等
    环境目录、dist/build 构建产物（它们不是"无用文件"）。
  - 符号链接只删链接本身，不跟随删除目标；任何候选 resolve 后必须在项目根内。
  - 默认 dry-run；--apply 也先扫描再删，删除前要求确认或 --yes。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    AI_DEV_DIR,
    HANDOFFS_DIR,
    ensure_stdio_safe,
    find_project_root,
    read_text,
)

# ---------------------------------------------------------------------------
# 清理规格（唯一权威清单；要调整只改这里，AI 按此规格行动）
# ---------------------------------------------------------------------------

# 目录名（任意深度匹配，整目录视为可再生缓存） -> 说明
SAFE_CACHE_DIRS: dict[str, str] = {
    "__pycache__": "Python 字节码缓存",
    ".pytest_cache": "pytest 运行缓存",
    ".mypy_cache": "mypy 类型检查缓存",
    ".ruff_cache": "ruff 缓存",
    ".eslintcache": "ESLint 缓存",
    ".parcel-cache": "Parcel 缓存",
    ".turbo": "Turborepo 缓存",
    ".vite": "Vite 缓存",
    ".nyc_output": "nyc 覆盖率输出",
    "htmlcov": "coverage HTML 报告（可再生）",
}

# 文件名（任意深度） -> 说明
SAFE_FILE_NAMES: dict[str, str] = {
    ".coverage": "coverage 数据文件（可再生）",
    ".DS_Store": "macOS 目录元数据",
    "Thumbs.db": "Windows 缩略图缓存",
}

# 文件后缀（任意深度） -> 说明
SAFE_FILE_SUFFIXES: dict[str, str] = {
    ".pyc": "Python 字节码",
    ".pyo": "Python 优化字节码",
    ".tmp": "临时文件",
    ".temp": "临时文件",
    ".tsbuildinfo": "TypeScript 增量编译缓存",
    ".orig": "合并/补丁备份残留",
    ".rej": "patch 拒绝残留",
    ".swp": "编辑器交换文件",
    ".swo": "编辑器交换文件",
}

# 谨慎类：可再生但体积大/重建慢，默认只列不删；--include-caution 才删
CAUTION_PATHS: dict[str, str] = {
    "node_modules/.cache": "npm 依赖缓存（大，可再生）",
    ".next/cache": "Next.js 构建缓存（大，可再生）",
    ".nuxt": "Nuxt 构建缓存（大，可再生）",
    ".tox": "tox 测试环境（重建慢）",
}

CAUTION_FILE_SUFFIXES: dict[str, str] = {
    ".log": "日志文件",
}

# 硬排除：整棵子树跳过，既不入列也不可删（这些不是"无用文件"）
HARD_EXCLUDE_NAMES = {
    ".git", AI_DEV_DIR, "specs", ".specify",
    "node_modules", "venv", ".venv", "env", ".env",
    "dist", "build", "coverage", ".gradle", ".idea",
}

# 只有明确在 CAUTION_PATHS 里的 node_modules 子路径才检查；node_modules 本体永远跳过
NODE_MODULES_CACHE = "node_modules/.cache"


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def _human_time(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


@dataclass
class _Cand:
    path: Path            # 相对项目根的路径
    is_dir: bool
    size: int             # 目录为估算总大小
    kind: str             # cache | temp-file | caution
    reason: str
    mtime: float


def _dir_size(p: Path) -> int:
    total = 0
    try:
        for _root, _dirs, files in os.walk(p, followlinks=False):
            for f in files:
                try:
                    total += (Path(_root) / f).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _scan(root: Path, include_caution: bool) -> list[_Cand]:
    cands: list[_Cand] = []
    seen: set[Path] = set()

    def add(p: Path, is_dir: bool, kind: str, reason: str) -> None:
        rel = p.relative_to(root)
        try:
            st = p.stat()
        except OSError:
            return
        size = _dir_size(p) if is_dir else st.st_size
        c = _Cand(rel, is_dir, size, kind, reason, st.st_mtime)
        # 同一目录只报一次（如 __pycache__ 下不再单列 .pyc）
        key = p.resolve()
        if key in seen:
            return
        seen.add(key)
        cands.append(c)

    # node_modules/.cache 特例：node_modules 整体跳过，但显式检查这一处
    if include_caution:
        nmc = root / NODE_MODULES_CACHE
        if nmc.is_dir():
            add(nmc, True, "caution", CAUTION_PATHS[NODE_MODULES_CACHE])

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        # 任何深度统一处理：硬排除剪枝；命中缓存规格的目录直接报告并剪枝
        keep = []
        for d in dirnames:
            if d in HARD_EXCLUDE_NAMES:
                continue
            if d in SAFE_CACHE_DIRS:
                add(dp / d, True, "cache", SAFE_CACHE_DIRS[d])
                continue  # 不再下钻（如 __pycache__ 里的 .pyc 不再单列）
            keep.append(d)
        dirnames[:] = keep

        for f in filenames:
            fp = dp / f
            if f in SAFE_FILE_NAMES:
                add(fp, False, "cache", SAFE_FILE_NAMES[f])
                continue
            for suf, reason in SAFE_FILE_SUFFIXES.items():
                if f.endswith(suf):
                    add(fp, False, "temp-file", reason)
                    break
            else:
                if include_caution:
                    for suf, reason in CAUTION_FILE_SUFFIXES.items():
                        if f.endswith(suf) and (fp.stat().st_size if fp.exists() else 0) > 1_000_000:
                            add(fp, False, "caution", reason + "（>1 MB）")
                            break

    return cands


# ---------------------------------------------------------------------------
# 上下文线索（读最近 N 轮 handoff 记录的 Verify 段，回答"这些缓存是不是这轮测试产生的"）
# ---------------------------------------------------------------------------

def _context_clues(root: Path, n: int) -> list[str]:
    idx = root / AI_DEV_DIR / HANDOFFS_DIR / "INDEX.md"
    if not idx.is_file():
        return []
    lines = [ln.strip() for ln in read_text(idx).splitlines() if ln.strip()]
    names: list[str] = []
    for ln in lines[-n:]:
        # INDEX 行形如 "| <文件名>.md | <agent> | <会话> | N |"，取其中含 .md 的段
        hit = next((seg.strip() for seg in ln.split("|") if seg.strip().endswith(".md")), "")
        if hit:
            names.append(hit)
    clues: list[str] = []
    for name in names:
        p = root / AI_DEV_DIR / HANDOFFS_DIR / name
        if not p.is_file():
            continue
        text = read_text(p)
        for sec in ("### Verify", "### Done", "### Risks"):
            if sec in text:
                seg = text.split(sec, 1)[1].split("###", 1)[0].strip()
                if seg:
                    clues.append(f"{name}  › {sec.lstrip('# ').strip()}：{seg[:120]}")
    return clues


# ---------------------------------------------------------------------------
# 输出与删除
# ---------------------------------------------------------------------------

def _print_report(root: Path, cands: list[_Cand], include_caution: bool,
                  context: list[str]) -> None:
    cache = [c for c in cands if c.kind == "cache"]
    temp = [c for c in cands if c.kind == "temp-file"]
    caution = [c for c in cands if c.kind == "caution"]
    total_size = sum(c.size for c in cands)

    print(f"devctx 收工清理 — {root}\n")
    if context:
        print("── 最近工作测试线索（来自 handoffs 压缩记录）──")
        for line in context:
            print(f"  {line}")
        print()

    def dump(items: list[_Cand], title: str) -> None:
        if not items:
            return
        print(f"── {title}（{len(items)} 个，约 {_human_size(sum(c.size for c in items))}）──")
        for c in sorted(items, key=lambda x: -x.mtime):
            kind = "目录" if c.is_dir else "文件"
            print(f"  {str(c.path):<56} {_human_size(c.size):>8}  {c.reason}  · {_human_time(c.mtime)}")
        print()

    dump(cache, "缓存类 · 建议删除（可再生）")
    dump(temp, "临时文件类 · 建议删除")
    if caution:
        dump(caution, "谨慎类 · 仅列出（需 --include-caution 才删）")

    if not cands:
        print("没有发现符合清理规格的候选文件。")
        return
    print(f"共 {len(cands)} 个候选，可释放约 {_human_size(total_size)}。")
    if caution and not include_caution:
        print(f"其中谨慎类 {len(caution)} 个（体积大/重建慢），默认不删；确认要删加 --include-caution。")
    print("默认只列不删。AI 应把上面清单展示给用户，用户同意后再执行 --apply。")


def _apply(root: Path, cands: list[_Cand], yes: bool, include_caution: bool) -> int:
    to_delete = [c for c in cands if c.kind != "caution"]
    if include_caution:
        to_delete = cands
    if not to_delete:
        print("没有待删除的候选。")
        return 0

    root_res = root.resolve()
    print(f"将删除 {len(to_delete)} 项（约 {_human_size(sum(c.size for c in to_delete))}）：")
    for c in to_delete:
        print(f"  {str(c.path):<56} {c.reason}")
    if not yes:
        ans = input("确认删除以上文件？[y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消，未删除任何文件。")
            return 1

    removed, failed = 0, 0
    for c in to_delete:
        abs_p = (root / c.path).resolve()
        try:
            abs_p.relative_to(root_res)  # 防符号链接逃逸
        except ValueError:
            print(f"[!] 跳过越界路径：{c.path}")
            failed += 1
            continue
        try:
            if c.is_dir:
                # 目录若是符号链接，只删链接本身，绝不 rmtree 目标
                if abs_p.is_symlink():
                    abs_p.unlink()
                else:
                    shutil.rmtree(abs_p)
            else:
                abs_p.unlink()
            removed += 1
        except OSError as e:
            print(f"[!] 删除失败 {c.path}: {e}")
            failed += 1
    print(f"已删除 {removed} 项" + (f"，失败 {failed} 项" if failed else "") + "。")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ensure_stdio_safe()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".", help="target project directory")
    ap.add_argument("--apply", action="store_true", help="按规格删除（默认 dry-run）")
    ap.add_argument("--yes", action="store_true", help="跳过删除前二次确认（仅配合 --apply）")
    ap.add_argument("--include-caution", action="store_true", help="把谨慎类也列入/删除")
    ap.add_argument("--context", type=int, default=3, metavar="N",
                    help="读最近 N 轮 handoff 记录做测试线索（默认 3；0 关闭）")
    ap.add_argument("--list-specs", action="store_true", help="打印清理规格后退出")
    args = ap.parse_args()

    if args.list_specs:
        print("缓存类目录：")
        for k, v in SAFE_CACHE_DIRS.items():
            print(f"  {k}  — {v}")
        print("缓存类文件：")
        for k, v in {**SAFE_FILE_NAMES, **{s: SAFE_FILE_SUFFIXES[s] for s in SAFE_FILE_SUFFIXES}}.items():
            print(f"  {k}  — {v}")
        print("谨慎类（默认不删，--include-caution 才删）：")
        for k, v in {**CAUTION_PATHS, **{s: CAUTION_FILE_SUFFIXES[s] for s in CAUTION_FILE_SUFFIXES}}.items():
            print(f"  {k}  — {v}")
        print("硬排除（永不扫描/删除）：" + ", ".join(sorted(HARD_EXCLUDE_NAMES)))
        return 0

    root = find_project_root(args.project)
    cands = _scan(root, args.include_caution)
    clues = _context_clues(root, args.context) if args.context > 0 else []

    if args.apply:
        return _apply(root, cands, args.yes, args.include_caution)
    _print_report(root, cands, args.include_caution, clues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
