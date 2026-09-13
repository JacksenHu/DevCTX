#!/usr/bin/env python3
"""Initialize the unified `.ai-dev/` context folder inside a target project.

Copies bundled templates (never overwriting existing content), auto-detects the
tech stack, builds the first code index, and syncs pointer files for detected
AI coding tools.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
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


def detect_stack(root: Path) -> dict:
    """Auto-detect tech stack from root-level marker files."""
    info = {
        "language": "待确认",
        "framework": "待确认",
        "storage": "待确认",
        "deploy": "待确认",
        "commands": {},
        "subprojects": [],
        "env_vars": [],
    }

    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(read_text(pkg))
            deps = data.get("dependencies", {})
            dev_deps = data.get("devDependencies", {})
            info["language"] = "TypeScript" if any(
                "typescript" in k for k in {**deps, **dev_deps}
            ) else "JavaScript"
            frameworks = [
                k for k in {**deps, **dev_deps}
                if k in ("next", "react", "vue", "nuxt", "svelte", "express",
                          "fastify", "nestjs", "angular", "@nestjs/core")
            ]
            info["framework"] = ", ".join(frameworks) if frameworks else "待确认"
            scripts = data.get("scripts", {})
            for cmd in ("dev", "start", "build", "test", "lint"):
                if cmd in scripts:
                    info["commands"][cmd] = f"npm run {cmd}"
        except Exception:
            pass

    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file() \
            or (root / "setup.py").is_file():
        info["language"] = "Python"
        for f in ("requirements.txt", "pyproject.toml"):
            p = root / f
            if not p.is_file():
                continue
            txt = read_text(p).lower()
            if "fastapi" in txt:
                info["framework"] = "FastAPI"
            elif "django" in txt:
                info["framework"] = "Django"
            elif "flask" in txt:
                info["framework"] = "Flask"
            break
        if "test" not in info["commands"]:
            info["commands"]["test"] = "pytest"
        if "lint" not in info["commands"]:
            info["commands"]["lint"] = "ruff check . && ruff format --check ."

    if (root / "go.mod").is_file():
        info["language"] = "Go"
        info["commands"]["build"] = "go build ./..."
        info["commands"]["test"] = "go test ./..."

    if (root / "Cargo.toml").is_file():
        info["language"] = "Rust"
        info["commands"]["build"] = "cargo build"
        info["commands"]["test"] = "cargo test"

    for parent_dir in ("packages", "apps", "services", "modules"):
        parent = root / parent_dir
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name.startswith("__"):
                continue
            sub_lang = None
            sub_framework = None
            if (child / "package.json").is_file():
                try:
                    sub_data = json.loads(read_text(child / "package.json"))
                    sub_lang = "TypeScript" if any(
                        "typescript" in k for k in {**sub_data.get("dependencies", {}),
                                                     **sub_data.get("devDependencies", {})}
                    ) else "JavaScript"
                    sub_deps = {**sub_data.get("dependencies", {}), **sub_data.get("devDependencies", {})}
                    fw = [k for k in sub_deps if k in ("next", "react", "vue", "nuxt", "svelte", "express", "nestjs")]
                    sub_framework = ", ".join(fw) if fw else None
                except Exception:
                    sub_lang = "JavaScript"
            elif (child / "requirements.txt").is_file() or (child / "pyproject.toml").is_file():
                sub_lang = "Python"
            elif (child / "go.mod").is_file():
                sub_lang = "Go"
            elif (child / "Cargo.toml").is_file():
                sub_lang = "Rust"
            if sub_lang:
                entry = f"{parent_dir}/{child.name}"
                if sub_framework:
                    entry += f" ({sub_lang}, {sub_framework})"
                else:
                    entry += f" ({sub_lang})"
                info["subprojects"].append(entry)

    env_example = root / ".env.example"
    if env_example.is_file():
        for line in read_text(env_example).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                info["env_vars"].append(key)

    deploy_markers = []
    if (root / "Dockerfile").is_file():
        deploy_markers.append("Docker")
    if (root / "docker-compose.yml").is_file() or (root / "docker-compose.yaml").is_file():
        deploy_markers.append("docker-compose")
    if (root / ".github" / "workflows").is_dir():
        deploy_markers.append("GitHub Actions")
    if deploy_markers:
        info["deploy"] = ", ".join(deploy_markers)

    return info


def fill_project_brief(template: str, root: Path) -> str:
    info = detect_stack(root)
    stack_table = (
        "| 层  | 选型 | 版本 | 备注 |\n"
        "| -- | -- | -- | -- |\n"
        f"| 语言 | {info['language']} | 待确认 | 自动检测，版本号请补 |\n"
        f"| 框架 | {info['framework']} | 待确认 | 自动检测，版本号请补 |\n"
        f"| 存储 | {info['storage']} | — | AI 扫描代码后补 |\n"
        f"| 部署 | {info['deploy']} | — | 自动检测部署方式 |\n"
    )

    cmd_lines = []
    cmd_map = {
        "install": "# 安装依赖",
        "run": "# 本地运行",
        "test": "# 运行测试",
        "lint": "# 代码检查 / 格式化",
        "build": "# 构建 / 部署",
    }
    for key, comment in cmd_map.items():
        if key in info["commands"]:
            cmd_lines.append(comment + "\n" + info["commands"][key])
        else:
            cmd_lines.append(comment + "\n# 待确认")
    commands_block = "\n\n".join(cmd_lines)

    old_table = (
        "| 层  | 选型 | 版本 | 备注 |\n"
        "| -- | -- | -- | -- |\n"
        "| 语言 |    |    |    |\n"
        "| 框架 |    |    |    |\n"
        "| 存储 |    |    |    |\n"
        "| 部署 |    |    |    |\n"
    )
    template = template.replace(old_table, stack_table)

    old_cmds = re.search(
        r"```text\n# 安装依赖\n\n# 本地运行\n\n# 运行测试\n\n# 代码检查 / 格式化\n\n# 构建 / 部署\n```",
        template,
    )
    if old_cmds:
        template = template[:old_cmds.start()] + "```text\n" + commands_block + "\n```" + template[old_cmds.end():]

    if info["subprojects"]:
        old_dirs = (
            "## 顶层目录结构\n\n"
            "- `src/` — <!-- 主源码目录职责 -->\n"
            "- `tests/` — <!-- 测试目录职责 -->\n"
        )
        new_dirs = "## 顶层目录结构\n\n"
        for sp in info["subprojects"]:
            new_dirs += f"- `{sp}/` — 自动检测的子项目\n"
        new_dirs += "- `src/` — <!-- 主源码目录职责（如无 monorepo 则保留） -->\n"
        new_dirs += "- `tests/` — <!-- 测试目录职责 -->\n"
        template = template.replace(old_dirs, new_dirs)

    if info["env_vars"]:
        old_env = (
            "## 环境与外部依赖\n\n"
            "- 环境变量样例位置：\n"
            "- 必需外部服务：\n"
            "- 密钥获取方式：\n"
        )
        env_list = "\n".join(f"  - `{v}`" for v in info["env_vars"])
        new_env = (
            "## 环境与外部依赖\n\n"
            f"- 环境变量样例位置：`.env.example`（自动检测到以下变量）\n"
            f"{env_list}\n"
            "- 必需外部服务：\n"
            "- 密钥获取方式：\n"
        )
        template = template.replace(old_env, new_env)

    return template


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
        if dest_rel == "project-brief.md":
            text = fill_project_brief(text, root)
        write_text(dest, text)
        print(f"[+] created {AI_DEV_DIR}/{dest_rel}")

    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "build_index.py"), str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(r.stdout.strip() or "(code index skipped)")
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)

    print("\n-- syncing tool pointer files --")
    actions = sync_rules.sync(root, mode=mode)
    sync_rules._print_report(actions)

    print(
        "\n[AI 必做，不要把空模板留给用户]\n"
        f"1. project-brief.md 的技术栈表格已由脚本自动预填（标注\"自动检测\"）；"
        "你只需补版本号、业务定位、存储和真实启动命令，不要推翻已检测到的事实。\n"
        f"2. 立即扫描代码（以 code-index.md 为地图），自动起草 {AI_DEV_DIR}/architecture.md、"
        "conventions.md、glossary.md（术语表，只留会被猜错的业务黑话/缩写）；"
        "lessons.md 初始可空，后续 HANDOFF 陷阱区某坑重复出现第二次再提升进来。"
        "事实只来自代码与用户，不确定标\"待确认\"，禁止编造。\n"
        "3. 用 architecture.md 写清模块分区与\"改什么去哪改\"速查。\n"
        "4. 初始化 HANDOFF.md 当前状态；然后用一句话告诉用户\"已接入，以后直接提需求即可\"。\n"
        "5. 用户无需手动运行任何脚本；其他 IDE 新开会话即由指针自动引导到 START_HERE.md。"
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
