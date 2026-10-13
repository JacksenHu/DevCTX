#!/usr/bin/env python3
"""Tests for the per-conversation compression spec and the devctx scripts.

Run with::

    python3 tests/test_devctx.py          # or: python3 -m unittest discover tests

Pure standard library, no network, no third-party deps. Each test builds a
throwaway project in a temp dir so the suite never touches real repos.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TEMPLATES = ROOT / "assets" / "templates"
sys.path.insert(0, str(SCRIPTS))

import log_handoff  # noqa: E402
import archive_handoff  # noqa: E402
import build_index  # noqa: E402
import doctor  # noqa: E402
import init_project  # noqa: E402
import doc_registry  # noqa: E402
import sync_rules  # noqa: E402
import retrofit  # noqa: E402
from _common import match_ignore  # noqa: E402


def _isolated_env(home: Path) -> dict[str, str]:
    """Point every home/appdata lookup at a throwaway dir.

    A HOME-only override leaks the real user's stores: the probe resolves
    Path.home() via USERPROFILE on Windows and editor stores via APPDATA.
    """
    return {**os.environ, "HOME": str(home),
            "USERPROFILE": str(home), "APPDATA": str(home)}


def _force_rmtree(path: Path) -> None:
    """rmtree that survives git's read-only object files on Windows."""
    for root, dirs, files in os.walk(path):
        for name in (*dirs, *files):
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path)


class ProjectFixture(unittest.TestCase):
    """A throwaway project with .ai-dev/ already initialised."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devctx-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / ".git").mkdir()
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "app.py").write_text('"""App entry."""\n', encoding="utf-8")
        self.ai = self.tmp / ".ai-dev"
        rc = init_project.init(self.tmp, "detected", False)
        self.assertEqual(rc, 0)

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), str(self.tmp), *args],
            capture_output=True, text=True, encoding="utf-8",
        )

    def write_record(self, user: str, agent: str, session: str,
                     done: str | None = None, nxt: str | None = None,
                     verify: str | None = None, risks: str | None = None,
                     intent: str | None = None,
                     model: str | None = None) -> Path:
        before = set((self.ai / "handoffs").glob("*.md"))
        path, sid = log_handoff.write_record(
            self.tmp, agent, session, user,
            log_handoff.build_body(done, nxt, verify, risks, intent,
                                   None, None, False),
            model,
        )
        self.assertTrue(path.is_file())
        self.assertEqual(sid, session)
        new = set((self.ai / "handoffs").glob("*.md")) - before
        self.assertEqual(len(new), 1, "exactly one record file per write")
        return path


# --------------------------------------------------------------- init / layout

class TestInit(ProjectFixture):
    def test_handoffs_dir_and_template_created(self):
        handoffs = self.ai / "handoffs"
        self.assertTrue((handoffs / "0000-template.md").is_file())
        self.assertTrue((handoffs / "INDEX.md").is_file())
        self.assertTrue((handoffs / "archive").is_dir())

    def test_pointer_mentions_handoffs(self):
        agents = self.tmp / "AGENTS.md"
        self.assertTrue(agents.is_file())
        text = agents.read_text(encoding="utf-8")
        self.assertIn("handoffs/INDEX.md", text)
        self.assertIn("每轮对话结束", text)

    def test_init_is_idempotent(self):
        before = sorted(p.name for p in (self.ai / "handoffs").glob("*"))
        self.assertEqual(init_project.init(self.tmp, "detected", False), 1)
        after = sorted(p.name for p in (self.ai / "handoffs").glob("*"))
        self.assertEqual(before, after)


# ------------------------------------------------------- compression spec

class TestCompressionSpec(ProjectFixture):
    def test_record_has_all_four_parts(self):
        p = self.write_record(
            "把按钮改成蓝色", "claude-code", "a1b2c3d4",
            done="改了 Hero.vue", nxt="- [ ] 跑测试", verify="npm test", risks="快照会挂",
        )
        text = p.read_text(encoding="utf-8")
        self.assertIn("devctx-schema: v2", text)
        self.assertIn("devctx-handoff: entry", text)
        for needle in ("时间戳", "Agent", "会话 ID", "用户原话", "### Done",
                       "### Next", "### Verify", "### Risks"):
            self.assertIn(needle, text)
        # 用户原话完整保留
        self.assertIn("> 把按钮改成蓝色", text)
        # ISO 8601 带时区
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")

    def test_sections_are_optional_but_one_is_required(self):
        # 任意组合：只给 Done + Risks
        p = self.write_record("查构建为什么慢", "gemini", "s1",
                              done="定位到全量 rebuild", risks="没试过拆 chunk")
        e = log_handoff.parse_entry(p)
        self.assertEqual(set(e.sections), {"Done", "Risks"})
        self.assertEqual(e.malformed, [])
        self.assertEqual(e.open_next, 0)

        # 一节都没有 -> 规范校验必须拒绝
        with self.assertRaises(SystemExit):
            log_handoff.build_body(None, None, None, None, None, None, None, False)
        # --force 才允许自由格式
        body = log_handoff.build_body(None, None, None, None, None, "随手记一笔", None, True)
        self.assertIn("随手记一笔", body)

    def test_open_next_is_counted(self):
        p = self.write_record("做登录", "cursor", "s2", done="一半",
                              nxt="- [ ] A\n- [ ] B\n- [x] C")
        e = log_handoff.parse_entry(p)
        self.assertEqual(e.open_next, 2)

    def test_user_quote_is_verbatim_multiline(self):
        user = "第一行需求\n第二行补充：不要动数据库\n\n第三段"
        p = self.write_record(user, "claude", "s3", done="x")
        e = log_handoff.parse_entry(p)
        self.assertEqual(e.user_text, user)

    def test_filenames_carry_agent_and_session(self):
        p = self.write_record("x", "claude-code", "a1b2c3d4", done="y")
        m = log_handoff.parse_entry(p)
        self.assertEqual(m.agent, "claude-code")
        self.assertEqual(m.session, "a1b2c3d4")
        self.assertIn("claude-code", p.name)
        self.assertIn("a1b2c3d4", p.name)


# ------------------------------------------------------------- concurrency

class TestConcurrency(ProjectFixture):
    def test_many_agents_do_not_overwrite_each_other(self):
        procs = []
        for i in range(8):
            procs.append(subprocess.Popen(
                [sys.executable, str(SCRIPTS / "log_handoff.py"), str(self.tmp),
                 "--user", f"并发任务 {i}", "--agent", f"agent{i}",
                 "--session", f"sess{i}", "--done", f"完成任务 {i}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            ))
        errs = [p.communicate()[1] for p in procs]
        for i, err in enumerate(errs):
            self.assertEqual(procs[i].returncode, 0, f"agent{i} failed: {err}")

        entries = log_handoff.scan_entries(self.ai / "handoffs")
        self.assertEqual(len(entries), 8)
        # 每条都完整可读，没有被截断或覆盖
        for i, e in enumerate(entries):
            self.assertTrue(e.user_text, f"{e.name} lost the user quote")
            self.assertIn("Done", e.sections)
        self.assertEqual(len({e.session for e in entries}), 8)

    def test_index_matches_disk_after_concurrent_writes(self):
        for i in range(6):
            subprocess.run(
                [sys.executable, str(SCRIPTS / "log_handoff.py"), str(self.tmp),
                 "--user", f"t{i}", "--agent", f"a{i}", "--session", f"s{i}",
                 "--done", "d"],
                capture_output=True, text=True, check=True,
            )
        entries = log_handoff.scan_entries(self.ai / "handoffs")
        idx = (self.ai / "handoffs" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn(f"条目数：{len(entries)}", idx)
        # 每个文件名都在索引里
        for e in entries:
            self.assertIn(e.name, idx)

    def test_same_second_same_identity_gets_suffix(self):
        p1 = self.write_record("a", "claude-code", "s", done="1")
        p2 = self.write_record("b", "claude-code", "s", done="2")
        self.assertNotEqual(p1.name, p2.name)
        self.assertEqual(len(log_handoff.scan_entries(self.ai / "handoffs")), 2)

    def test_agent_autodetect_from_project_evidence(self):
        (self.tmp / ".cursor").mkdir()
        self.assertEqual(log_handoff.detect_agent(self.tmp), "cursor")
        self.assertEqual(log_handoff.detect_agent(self.tmp, "explicit"), "explicit")
        os.environ["DEVCTX_AGENT"] = "from-env"
        try:
            self.assertEqual(log_handoff.detect_agent(self.tmp), "from-env")
        finally:
            del os.environ["DEVCTX_AGENT"]


# ------------------------------------------------------------------ reading

class TestReading(ProjectFixture):
    def test_tail_returns_newest_first(self):
        for i in range(4):
            self.write_record(f"任务{i}", "claude", "s", done=f"d{i}")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = log_handoff.cmd_tail(self.ai / "handoffs", 2, None)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("任务3", out)
        self.assertIn("任务2", out)
        self.assertNotIn("任务1", out)

    def test_tail_filtered_by_agent(self):
        self.write_record("我的活", "cursor", "s1", done="d")
        self.write_record("别人的活", "gemini", "s2", done="d")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            log_handoff.cmd_tail(self.ai / "handoffs", 5, "cursor")
        self.assertIn("我的活", buf.getvalue())
        self.assertNotIn("别人的活", buf.getvalue())

    def test_list_and_empty(self):
        self.assertEqual(log_handoff.cmd_list(self.ai / "handoffs"), 1)  # 还没有记录
        self.write_record("x", "a", "s", done="d")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(log_handoff.cmd_list(self.ai / "handoffs"), 0)
        self.assertIn("1 条记录", buf.getvalue())


# ------------------------------------------------------------------ doctor

class TestDoctor(ProjectFixture):
    def test_detects_index_drift_and_fixes_it(self):
        self.write_record("x", "a", "s", done="d")
        (self.ai / "handoffs" / "INDEX.md").unlink()
        findings = doctor.run_doctor(self.tmp, False)
        drift = [f for f in findings if f.area == "handoffs" and "unreadable" in f.msg]
        self.assertTrue(drift, "索引缺失必须被报告")

        findings = doctor.run_doctor(self.tmp, True)  # --fix
        rebuilt = [f for f in findings if f.area == "handoffs" and "rebuilt" in f.msg]
        self.assertTrue(rebuilt, "--fix 必须重建索引")
        self.assertTrue((self.ai / "handoffs" / "INDEX.md").is_file())

    def test_detects_spec_violations_by_filename(self):
        self.write_record("x", "a", "s", done="d")
        bad = self.ai / "handoffs" / "20260101-000000-broken-x.md"
        bad.write_text(
            "<!-- devctx-schema: v2 -->\n<!-- devctx-handoff: entry -->\n\n"
            "# 对话压缩记录\n\n## 用户原话\n\n## 本轮压缩\n", encoding="utf-8")
        findings = doctor.run_doctor(self.tmp, False)
        msgs = [f.msg for f in findings if f.area == "handoffs"]
        self.assertTrue(any("broken-x" in m for m in msgs), msgs)
        self.assertTrue(any("缺用户原话" in m for m in msgs), msgs)

    def test_detects_handoff_older_than_records(self):
        self.write_record("x", "a", "s", done="d")
        h = self.ai / "HANDOFF.md"
        old = _dt.datetime.now().timestamp() - 3600
        os.utime(h, (old, old))
        findings = doctor.run_doctor(self.tmp, False)
        self.assertTrue(any("older than the newest" in f.msg
                            for f in findings if f.area == "handoffs"))

    def test_missing_handoffs_dir_is_reported_and_fixed(self):
        shutil.rmtree(self.ai / "handoffs")
        findings = doctor.run_doctor(self.tmp, False)
        self.assertTrue(any("missing" in f.msg for f in findings if f.area == "handoffs"))
        findings = doctor.run_doctor(self.tmp, True)
        self.assertTrue((self.ai / "handoffs" / "INDEX.md").is_file())

    def test_exit_codes(self):
        r = self.run_script("doctor.py")
        self.assertIn(r.returncode, (0, 1, 2))


# ------------------------------------------------------------------ archive

class TestArchive(ProjectFixture):
    def test_only_oldest_entries_are_archived(self):
        for i in range(6):
            self.write_record(f"任务{i}", "a", "s", done="d")
        moved = archive_handoff.archive_entries(self.tmp, keep=3)
        self.assertEqual(moved, 3)
        entries = log_handoff.scan_entries(self.ai / "handoffs")
        self.assertEqual(len(entries), 3)
        # 保留的是最新的三条
        self.assertIn("任务5", entries[-1].user_text)
        self.assertIn("任务4", entries[-2].user_text)
        self.assertIn("任务3", entries[-3].user_text)
        # 归档文件存在且含被搬走的记录
        archived = list((self.ai / "archive" / "handoffs").glob("*.md"))
        self.assertEqual(len(archived), 1)
        self.assertIn("任务2", archived[0].read_text(encoding="utf-8"))
        # 索引同步
        idx = (self.ai / "handoffs" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("条目数：3", idx)
        self.assertNotIn("任务2", idx.split("| 条目文件 |")[-1])

    def test_archive_is_noop_under_budget(self):
        for i in range(3):
            self.write_record(f"t{i}", "a", "s", done="d")
        self.assertEqual(archive_handoff.archive_entries(self.tmp, keep=200), 0)

    def test_cli_entries_only(self):
        for i in range(5):
            self.write_record(f"t{i}", "a", "s", done="d")
        r = self.run_script("archive_handoff.py", "--max-entries", "2", "--entries-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(log_handoff.scan_entries(self.ai / "handoffs")), 2)


# -------------------------------------------------------------- ignore globs

class TestIgnoreMatching(unittest.TestCase):
    def test_double_star_matches_any_depth(self):
        pats = ["**/node_modules", "**/*.min.js", "assets/third-party/"]
        self.assertTrue(match_ignore(("a", "b", "node_modules"), pats))
        self.assertTrue(match_ignore(("node_modules",), pats))
        self.assertTrue(match_ignore(("a", "x.min.js"), pats))
        self.assertTrue(match_ignore(("assets", "third-party", "lib.js"), pats))
        self.assertFalse(match_ignore(("src", "app.js"), pats))

    def test_leading_slash_anchors_to_root(self):
        pats = ["/build", "/dist/"]
        self.assertTrue(match_ignore(("build",), pats))
        self.assertTrue(match_ignore(("dist", "out.js"), pats))
        self.assertFalse(match_ignore(("packages", "build"), pats))
        self.assertFalse(match_ignore(("src", "dist"), pats))

    def test_negation_last_wins(self):
        pats = ["*.log", "!keep.log"]
        self.assertTrue(match_ignore(("a.log",), pats))
        self.assertFalse(match_ignore(("keep.log",), pats))

    def test_directory_pattern_covers_children(self):
        pats = ["__pycache__/"]
        self.assertTrue(match_ignore(("a", "__pycache__", "x.pyc"), pats))
        self.assertTrue(match_ignore(("__pycache__",), pats))


# ------------------------------------------------------------ build_index

class TestBuildIndex(ProjectFixture):
    def test_index_respects_ignore_conf(self):
        (self.tmp / "node_modules").mkdir()
        (self.tmp / "node_modules" / "junk.js").write_text("//junk\n", encoding="utf-8")
        (self.tmp / "debug.log").write_text("noise\n", encoding="utf-8")
        r = self.run_script("build_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.ai / "code-index.md").read_text(encoding="utf-8")
        self.assertNotIn("node_modules", text)
        self.assertNotIn("debug.log", text)
        self.assertIn("app.py", text)

    def test_handoffs_not_in_code_index(self):
        self.write_record("x", "a", "s", done="d")
        self.run_script("build_index.py")
        text = (self.ai / "code-index.md").read_text(encoding="utf-8")
        self.assertNotIn("handoffs", text)


# ---------------------------------------------------------------- sync_rules

class TestSyncRules(ProjectFixture):
    def test_handwritten_rules_are_preserved(self):
        target = self.tmp / "CLAUDE.md"
        target.write_text("# 我的私有规则\n不要改这里\n", encoding="utf-8")
        actions = sync_rules.sync(self.tmp, mode="detected")
        claude = [a for a in actions if a.rel == "CLAUDE.md"][0]
        self.assertEqual(claude.status, "conflict")
        self.assertIn("我的私有规则", target.read_text(encoding="utf-8"))

        # --inject 只加标记块，块外内容不丢
        actions = sync_rules.sync(self.tmp, mode="detected", inject=True)
        claude = [a for a in actions if a.rel == "CLAUDE.md"][0]
        self.assertEqual(claude.status, "injected")
        text = target.read_text(encoding="utf-8")
        self.assertIn("我的私有规则", text)
        self.assertIn("devctx:begin", text)
        self.assertIn("handoffs/INDEX.md", text)

        # 幂等：再跑一次不重复注入
        sync_rules.sync(self.tmp, mode="detected", inject=True)
        self.assertEqual(text, target.read_text(encoding="utf-8"))


# ============================================================ 文档治理 + 需求受理
#
# 这一组测试守的是"防漂移"契约：
#   - 一个需求只对应一份文档，用户插话/追加/更正必须落到同一份文档上
#   - 动手前的 intake 裁定有证据、有优先级，不能把"改需求"判成"新功能"
#   - 裁定结论写进 handoff 的 ### Intent，换 Agent 后能读回来

def _make_doc(root: Path, name: str, topic: str, status: str = "active",
              source: str = "NEW（2026-10-12 首次提出）") -> Path:
    """照模板造一份合规文档（不经过 --add，模拟 AI 手工创建）。"""
    tpl = (TEMPLATES / "doc-template.md").read_text(encoding="utf-8")
    p = root / ".ai-dev" / "docs" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(tpl, encoding="utf-8")
    doc_registry.cmd_add(root, f".ai-dev/docs/{name}", topic, source, status)
    return p


class TestDocRegistry(unittest.TestCase):
    """DOCS.md 是唯一登记口：不登记就存在 = 错误级问题。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devctx-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / ".git").mkdir()
        rc = init_project.init(self.tmp, "detected", False)
        self.assertEqual(rc, 0)

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), str(self.tmp), *args],
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_init_lays_down_template_and_index(self) -> None:
        docs = self.tmp / ".ai-dev" / "docs"
        self.assertTrue((docs / "0000-template.md").is_file())
        idx = docs / "DOCS.md"
        self.assertTrue(idx.is_file())
        text = idx.read_text(encoding="utf-8")
        self.assertIn("devctx-docs: index", text)
        self.assertIn("一个需求只对应一份文档", text)

    def test_add_registers_topic_and_source(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        idx = (self.tmp / ".ai-dev" / "docs" / "DOCS.md").read_text(encoding="utf-8")
        self.assertIn("auth-sms-login.md", idx)
        self.assertIn("短信验证码登录", idx)
        doc = (self.tmp / ".ai-dev" / "docs" / "auth-sms-login.md").read_text(encoding="utf-8")
        self.assertIn("| 主题 | 短信验证码登录 |", doc)
        self.assertIn("| 状态 | active |", doc)

    def test_unregistered_doc_is_flagged(self) -> None:
        # AI 绕过 DOCS.md 直接落盘的文档，check 必须抓到
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        rogue = self.tmp / ".ai-dev" / "docs" / "rogue.md"
        rogue.write_text("# 私自建的文档\n", encoding="utf-8")
        r = self.run_script("doc_registry.py", "--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("rogue.md", r.stdout)
        self.assertIn("未登记", r.stdout)

    def test_duplicate_topic_is_flagged(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        _make_doc(self.tmp, "login-sms.md", "短信验证码登录")
        r = self.run_script("doc_registry.py", "--check")
        self.assertEqual(r.returncode, 2)
        self.assertIn("主题重复", r.stdout)

    def test_add_fills_missing_field_table(self) -> None:
        # AI 手工放的文档没有字段表：--add 必须补行/补表，不能静默半登记
        bare = self.tmp / ".ai-dev" / "docs" / "bare.md"
        bare.write_text("<!-- devctx-docs: doc -->\n# 光秃秃的文档\n只有一句话。\n",
                        encoding="utf-8")
        doc_registry.cmd_add(self.tmp, ".ai-dev/docs/bare.md", "光秃主题",
                             "RETROFIT 2026-10-12", "active")
        d = doc_registry.parse_doc(bare)
        self.assertEqual(d.topic, "光秃主题")
        self.assertEqual(d.status, "active")
        self.assertIn("RETROFIT", d.source)
        self.assertEqual(d.malformed, [])
        idx = (self.tmp / ".ai-dev" / "docs" / "DOCS.md").read_text(encoding="utf-8")
        row = [ln for ln in idx.splitlines() if "bare.md" in ln][0]
        self.assertIn("光秃主题", row)
        self.assertNotIn("（缺）", row)

    def test_find_hits_and_misses(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        r = self.run_script("doc_registry.py", "--find", "验证码")
        self.assertEqual(r.returncode, 0)
        self.assertIn("auth-sms-login.md", r.stdout)
        r = self.run_script("doc_registry.py", "--find", "支付退款")
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有文档命中", r.stdout)

    def test_archive_moves_doc_and_rebuilds(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        r = self.run_script("doc_registry.py", "--archive", ".ai-dev/docs/auth-sms-login.md")
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.tmp / ".ai-dev" / "docs" / "auth-sms-login.md").exists())
        moved = list((self.tmp / ".ai-dev" / "archive" / "docs").glob("auth-sms-login-*.md"))
        self.assertEqual(len(moved), 1)
        self.assertIn("archived", moved[0].read_text(encoding="utf-8"))

    def test_rebuild_repairs_index_drift(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        idx = self.tmp / ".ai-dev" / "docs" / "DOCS.md"
        idx.write_text("<!-- devctx-docs: index -->\n# 被手改坏的索引\n", encoding="utf-8")
        r = self.run_script("doc_registry.py", "--check")
        self.assertEqual(r.returncode, 2)
        r = self.run_script("doc_registry.py", "--rebuild")
        self.assertEqual(r.returncode, 0)
        self.assertIn("auth-sms-login.md", idx.read_text(encoding="utf-8"))
        r = self.run_script("doc_registry.py", "--check")
        self.assertEqual(r.returncode, 0)

    def test_index_counts_linking_records(self) -> None:
        # 回归：rebuild_index 必须自己算关联记录数，
        # 否则调用方预设的 links 被二次 scan 丢弃，索引永远显示 0
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        log_handoff.write_record(
            self.tmp, "claude-code", "s1", "短信验证码登录收不到验证码",
            log_handoff.build_body("查了网关", None, None, None,
                                   "EXTEND → docs/auth-sms-login.md",
                                   None, None, False),
            None,
        )
        n, idx = doc_registry.rebuild_index(self.tmp / ".ai-dev" / "docs")
        self.assertEqual(n, 1)
        row = [ln for ln in idx.read_text(encoding="utf-8").splitlines()
               if "auth-sms-login.md" in ln][0]
        self.assertTrue(row.rstrip().endswith("| 1 |"), row)

    def test_doctor_flags_missing_topic_or_source(self) -> None:
        bad = self.tmp / ".ai-dev" / "docs" / "bad.md"
        bad.write_text("<!-- devctx-docs: doc -->\n# 没有字段表的文档\n", encoding="utf-8")
        doc_registry.cmd_add(self.tmp, ".ai-dev/docs/bad.md", "坏文档", "NEW（测试）", "active")
        # cmd_add 会把字段补上；再造一份连标记都没有的
        worse = self.tmp / ".ai-dev" / "docs" / "worse.md"
        worse.write_text("# 什么都没有\n", encoding="utf-8")
        doc_registry.rebuild_index(self.tmp / ".ai-dev" / "docs")
        d = doc_registry.parse_doc(worse)
        self.assertTrue(d.malformed)


class TestIntake(unittest.TestCase):
    """受理裁定的证据与优先级：改需求绝不能判成新功能。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devctx-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / ".git").mkdir()
        (self.tmp / "src").mkdir()
        rc = init_project.init(self.tmp, "detected", False)
        self.assertEqual(rc, 0)

    def run_intake(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "doc_registry.py"), str(self.tmp),
             "--intake", *args],
            capture_output=True, text=True, encoding="utf-8",
        )

    def write_record(self, user: str, nxt: str | None = None,
                     agent: str = "claude-code", session: str = "s1") -> None:
        log_handoff.write_record(
            self.tmp, agent, session, user,
            log_handoff.build_body("做了一半", nxt, None, None, None, None, None, False),
            None,
        )

    def test_brand_new_topic_is_new(self) -> None:
        r = self.run_intake("--user", "帮我导出一份 CSV 报表")
        self.assertEqual(r.returncode, 0)
        self.assertIn("建议判定：**NEW**", r.stdout)
        self.assertIn("三类证据均未覆盖", r.stdout)

    def test_active_doc_is_extend(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录", "active")
        r = self.run_intake("--user", "短信验证码登录收不到验证码，看看是不是网关的问题")
        self.assertIn("建议判定：**EXTEND**", r.stdout)
        self.assertIn("docs/auth-sms-login.md", r.stdout)
        self.assertIn("改它，禁止新建", r.stdout)

    def test_interruption_extends_same_doc_never_new(self) -> None:
        # 用户做到一半插话/追加：仍然落回同一份文档，绝不开第二份
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录", "active")
        self.write_record("短信验证码登录做了一半", nxt="- [ ] 补验证码频率限制")
        r = self.run_intake("--user", "验证码登录先别用手机号了，改用邮箱")
        self.assertIn("建议判定：**EXTEND**", r.stdout)
        self.assertIn("docs/auth-sms-login.md", r.stdout)
        self.assertNotIn("NEW", r.stdout.split("含义")[0].replace("建议判定：**EXTEND**", ""))

    def test_stable_doc_change_is_extend(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录", "stable")
        r = self.run_intake("--user", "短信验证码登录要加图形验证码防刷")
        self.assertIn("建议判定：**EXTEND**", r.stdout)
        self.assertIn("状态改回 active", r.stdout)

    def test_open_next_without_doc_is_continue(self) -> None:
        self.write_record("短信验证码登录做了一半", nxt="- [ ] 补频率限制")
        r = self.run_intake("--user", "接着把短信验证码登录的收尾工作做完")
        self.assertIn("建议判定：**CONTINUE**", r.stdout)
        self.assertIn("补一份文档", r.stdout)

    def test_unrelated_utterance_is_not_continue(self) -> None:
        # 防误判：有开放 Next 讲登录，用户说导出报表 -> 仍是 NEW
        self.write_record("短信验证码登录做了一半", nxt="- [ ] 补频率限制")
        r = self.run_intake("--user", "帮我导出一份 CSV 报表")
        self.assertIn("建议判定：**NEW**", r.stdout)

    def test_specs_tasks_continue(self) -> None:
        specs = self.tmp / "specs" / "auth-sms-login" / "tasks.md"
        specs.parent.mkdir(parents=True)
        specs.write_text("# tasks\n- [x] 搭页面\n- [ ] 接网关\n", encoding="utf-8")
        r = self.run_intake("--user", "短信验证码登录的网关还没接")
        self.assertIn("建议判定：**CONTINUE**", r.stdout)
        self.assertIn("tasks.md", r.stdout)

    def test_active_doc_beats_open_record(self) -> None:
        # 优先级：有 active 文档时必须落到文档，记录只是过程流水
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录", "active")
        self.write_record("短信验证码登录做了一半", nxt="- [ ] 补频率限制")
        r = self.run_intake("--user", "短信验证码登录再加个图形验证码")
        self.assertIn("建议判定：**EXTEND**", r.stdout)
        self.assertIn("docs/auth-sms-login.md", r.stdout)

    def test_intake_prints_evidence(self) -> None:
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录", "active")
        r = self.run_intake("--user", "短信验证码登录收不到验证码")
        self.assertIn("[现有文档]", r.stdout)
        self.assertIn("相关度", r.stdout)
        self.assertIn("下一步（按顺序）", r.stdout)


class TestIntentSection(unittest.TestCase):
    """裁定结论必须写进 ### Intent，换 Agent 后读得回来。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devctx-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / ".git").mkdir()
        (self.tmp / "src").mkdir()
        rc = init_project.init(self.tmp, "detected", False)
        self.assertEqual(rc, 0)

    def test_intent_round_trips_through_scan(self) -> None:
        path, _ = log_handoff.write_record(
            self.tmp, "claude-code", "s1", "短信验证码登录收不到验证码",
            log_handoff.build_body("查了网关", "- [ ] 补监控", None, None,
                                   "EXTEND → docs/auth-sms-login.md", None, None, False),
            None,
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("### Intent", text)
        self.assertIn("EXTEND → docs/auth-sms-login.md", text)
        entries = log_handoff.scan_entries(self.tmp / ".ai-dev" / "handoffs")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].sections.get("Intent"),
                         "EXTEND → docs/auth-sms-login.md")
        self.assertEqual(entries[0].malformed, [])

    def test_intent_only_record_is_malformed(self) -> None:
        # 光有裁定没有压缩内容，不算有效记录——doctor 要能抓出来
        path, _ = log_handoff.write_record(
            self.tmp, "claude-code", "s1", "随便说一句",
            log_handoff.build_body(None, None, None, None,
                                   "NEW → docs/x.md", None, None, False),
            None,
        )
        entries = log_handoff.scan_entries(self.tmp / ".ai-dev" / "handoffs")
        self.assertEqual(len(entries), 1)
        self.assertIn("无 Done/Next/Verify/Risks 任一节", entries[0].malformed)

    def test_intake_verdict_lands_in_next_record(self) -> None:
        # 端到端：受理 → 按裁定改同一份文档 → 记录带 Intent 指向该文档
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录", "active")
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "doc_registry.py"), str(self.tmp),
             "--intake", "--user", "短信验证码登录再加图形验证码"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn("docs/auth-sms-login.md", r.stdout)
        path, _ = log_handoff.write_record(
            self.tmp, "claude-code", "s2", "短信验证码登录再加图形验证码",
            log_handoff.build_body("在需求变更里追加了本次原话", None, "跑了 test_intake",
                                   None, "EXTEND → docs/auth-sms-login.md",
                                   None, None, False),
            None,
        )
        links = doc_registry.count_links(self.tmp, "auth-sms-login.md")
        self.assertEqual(links, 1)



# ==================================================== 存量多 Agent 项目接入（retrofit）
#
# 这一组守"接手遗留项目"的契约：git 历史要能切成历史会话画像并回填成
# 合规记录；存量文档的归并建议要看正文（标题常常不包含主题）；--apply
# 的"未登记"必须对比 DOCS.md 索引而不是磁盘扫描。

def _commit(root: Path, msg: str, name: str, email: str, date: str,
            touch: str) -> None:
    f = root / touch
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"# {msg}\n")
    env = {
        "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
        "PATH": os.environ.get("PATH", ""),
    }
    subprocess.run(["git", "-C", str(root), "add", "-A"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", msg],
                   capture_output=True, env=env, check=True)


class TestRetrofit(unittest.TestCase):
    """A legacy project built by several agents/sessions gets onboarded."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devctx-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        subprocess.run(["git", "init", "-q", str(self.tmp)],
                       capture_output=True, check=True)
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "app.py").write_text(
            '"""app"""\n# TODO: 抽离配置\n', encoding="utf-8")
        (self.tmp / "src" / "auth.py").write_text(
            '"""sms login"""\n# FIXME: 网关未接\n', encoding="utf-8")
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "api.md").write_text(
            "# API 约定\n短信验证码登录接口：POST /auth/sms\n", encoding="utf-8")
        (self.tmp / "README.md").write_text("# 遗留项目说明\n", encoding="utf-8")
        _commit(self.tmp, "初始化项目骨架", "Alice", "alice@x.com",
                "2026-03-01T10:00:00", "src/app.py")
        _commit(self.tmp, "短信验证码登录 搭页面", "Bob", "bob@x.com",
                "2026-06-10T14:00:00", "src/auth.py")
        _commit(self.tmp, "验证码登录 网关联调", "Bob", "bob@x.com",
                "2026-06-12T09:30:00", "src/auth.py")
        _commit(self.tmp, "导出报表 需求新增", "Alice", "alice@x.com",
                "2026-09-20T16:00:00", "src/app.py")
        rc = init_project.init(self.tmp, "detected", False)
        self.assertEqual(rc, 0)

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "retrofit.py"), str(self.tmp), *args],
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_scan_mines_sessions_docs_and_stubs(self) -> None:
        r = self.run_script("scan")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Alice 三月一次、九月一次 → 两个画像；Bob 六月两次相邻 → 一个
        self.assertIn("历史会话画像（3 个）", r.stdout)
        self.assertIn("legacy-alice-1", r.stdout)
        self.assertIn("legacy-alice-2", r.stdout)
        self.assertIn("legacy-bob", r.stdout)
        self.assertIn("docs/api.md", r.stdout)
        self.assertIn("半成品信号", r.stdout)
        self.assertIn("2 处", r.stdout)          # TODO + FIXME

    def test_scan_without_git_is_graceful(self) -> None:
        _force_rmtree(self.tmp / ".git")
        r = self.run_script("scan")
        self.assertEqual(r.returncode, 0)
        self.assertIn("不是 git 仓库", r.stdout)
        self.assertIn("docs/api.md", r.stdout)    # 文档清单仍然可用

    def test_plan_lists_ordered_steps(self) -> None:
        r = self.run_script("plan")
        self.assertEqual(r.returncode, 0)
        for i in range(1, 8):
            self.assertIn(f"{i}. ", r.stdout)
        self.assertIn("baseline", r.stdout)
        self.assertIn("consolidate", r.stdout)

    def test_plan_offers_real_session_ids(self) -> None:
        """plan 输出必须主动给'真实会话 Id 可精确回填'的选项，且保留禁止编造红线。"""
        r = self.run_script("plan")
        self.assertEqual(r.returncode, 0)
        self.assertIn("历史会话记录", r.stdout)
        self.assertIn("禁止编造", r.stdout)

    def test_baseline_record_is_valid_and_indexed(self) -> None:
        r = self.run_script("baseline", "--session", "legacy-bob",
                            "--topic", "auth-sms-login",
                            "--user", "把短信验证码登录的网关卡打完",
                            "--done", "搭了验证码页面，网关联调中",
                            "--next", "- [ ] 接完验证码网关",
                            "--risks", "网关文档过期")
        self.assertEqual(r.returncode, 0, r.stderr)
        entries = log_handoff.scan_entries(self.tmp / ".ai-dev" / "handoffs")
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.agent, "retrofit")
        self.assertEqual(e.session, "legacy-bob")
        self.assertEqual(e.malformed, [])
        self.assertIn("把短信验证码登录的网关卡打完", e.user_text)
        self.assertEqual(e.open_next, 1)
        self.assertIn("RETROFIT → docs/auth-sms-login.md",
                      e.sections.get("Intent", ""))
        idx = (self.tmp / ".ai-dev" / "handoffs" / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("legacy-bob", idx)

    def test_baseline_requires_user_text(self) -> None:
        r = self.run_script("baseline", "--session", "legacy-bob",
                            "--topic", "auth-sms-login", "--done", "做了点")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--user", r.stdout + r.stderr)

    def test_consolidate_suggests_merge_by_body(self) -> None:
        # 标题「API 约定」不含主题词，正文才有——建议必须看正文
        _make_doc(self.tmp, "auth-sms-login.md", "短信验证码登录")
        r = self.run_script("consolidate")
        self.assertEqual(r.returncode, 0)
        self.assertIn("docs/api.md", r.stdout)
        self.assertIn("docs/auth-sms-login.md", r.stdout)
        self.assertIn("建议并入", r.stdout)

    def test_consolidate_apply_registers_unregistered(self) -> None:
        rogue = self.tmp / ".ai-dev" / "docs" / "legacy-note.md"
        rogue.write_text("# 遗留笔记\n", encoding="utf-8")
        r = self.run_script("consolidate")
        self.assertIn("legacy-note.md", r.stdout)
        self.assertIn("dry-run", r.stdout)
        r = self.run_script("consolidate", "--apply")
        self.assertEqual(r.returncode, 0)
        idx = (self.tmp / ".ai-dev" / "docs" / "DOCS.md").read_text(encoding="utf-8")
        self.assertIn("legacy-note.md", idx)
        self.assertIn("RETROFIT", idx)
        # 登记后 doctor 的 docs 检查不再报未登记
        r = self.run_script("consolidate")
        self.assertIn("全部已登记", r.stdout)


# ------------------------------------------------------- recall: find / gaps

class TestRecall(ProjectFixture):
    """--find 按会话 Id 定位、--gaps 时间线缺口：回答"哪里没了记忆"。"""

    def _record_at(self, when: _dt.datetime, agent: str, session: str, user: str) -> Path:
        p = self.ai / "handoffs" / f"{when.strftime('%Y%m%d-%H%M%S')}-{agent}-{session}.md"
        p.write_text(
            "<!-- devctx-schema: v2 -->\n<!-- devctx-handoff: entry -->\n\n"
            "# 对话压缩记录\n\n"
            "| 字段 | 值 |\n|---|---|\n"
            f"| 时间戳 | {when.isoformat()} |\n"
            f"| Agent | {agent} |\n"
            f"| 会话 ID | {session} |\n"
            "| 模型 | — |\n\n"
            "## 用户原话\n\n"
            f"> {user}\n\n"
            "## 本轮压缩\n\n### Done\n\n做了点事\n",
            encoding="utf-8")
        os.utime(p, (when.timestamp(), when.timestamp()))
        return p

    def test_find_locates_session_by_id(self) -> None:
        now = _dt.datetime.now()
        self._record_at(now - _dt.timedelta(days=5), "claude-code", "a1b2c3d4", "把登录改成手机号")
        self._record_at(now - _dt.timedelta(days=2), "cursor", "ff00ee11", "修导航样式")
        r = self.run_script("log_handoff.py", "--find", "a1b2c3d4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 条匹配", r.stdout)
        self.assertIn("把登录改成手机号", r.stdout)
        self.assertIn("读全文：handoffs/", r.stdout)
        self.assertNotIn("修导航样式", r.stdout)

    def test_find_matches_agent_name_too(self) -> None:
        now = _dt.datetime.now()
        self._record_at(now - _dt.timedelta(days=3), "cursor", "ff00ee11", "修导航样式")
        self._record_at(now - _dt.timedelta(days=1), "claude-code", "a1b2c3d4", "把登录改成手机号")
        r = self.run_script("log_handoff.py", "--find", "cursor")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 条匹配", r.stdout)
        self.assertIn("修导航样式", r.stdout)
        self.assertNotIn("把登录改成手机号", r.stdout)

    def test_find_miss_is_honest(self) -> None:
        r = self.run_script("log_handoff.py", "--find", "nosuch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有匹配", r.stdout)

    def test_gaps_reports_silent_stretch(self) -> None:
        now = _dt.datetime.now()
        self._record_at(now - _dt.timedelta(days=30), "claude-code", "old11111", "早期需求")
        self._record_at(now - _dt.timedelta(days=1), "claude-code", "a1b2c3d4", "把登录改成手机号")
        r = self.run_script("log_handoff.py", "--gaps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 个无记录区间", r.stdout)
        self.assertIn("间隔 29 天", r.stdout)
        self.assertIn("old11111", r.stdout)          # 断点前最后一条要能定位

    def test_gaps_silent_when_dense(self) -> None:
        now = _dt.datetime.now()
        self._record_at(now - _dt.timedelta(days=2), "claude-code", "old11111", "早期需求")
        self._record_at(now - _dt.timedelta(hours=2), "claude-code", "a1b2c3d4", "把登录改成手机号")
        r = self.run_script("log_handoff.py", "--gaps")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("无超过 7 天", r.stdout)

    def test_gaps_needs_two_records(self) -> None:
        r = self.run_script("log_handoff.py", "--gaps")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不足两条", r.stdout)


class TestProbe(unittest.TestCase):
    """Local session-store probing: discovery only, never message bodies."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devctx-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "app.py").write_text('"""app"""\n', encoding="utf-8")

    def run_probe(self) -> subprocess.CompletedProcess:
        env = _isolated_env(self.home)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "retrofit.py"), str(self.tmp), "probe"],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )

    def _claude_session(self, sid: str, day: str, cwd: str | None = None) -> None:
        # 用实现同一个编码器：只换 "/" 在 Windows 上会把 "C:\..." 当成多级目录名
        enc = retrofit._claude_encoded(cwd or self.tmp)
        d = self.home / ".claude" / "projects" / enc
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.jsonl").write_text(
            '{"sessionId":"%s","cwd":%s,"timestamp":"2026-10-%sT10:00:00.000Z"}\n'
            '{"role":"user","content":"这是消息正文，不许被探测读走"}\n'
            % (sid, json.dumps(str(cwd or self.tmp)), day),
            encoding="utf-8")

    def test_probe_finds_claude_sessions_scoped(self) -> None:
        self._claude_session("a1b2c3d4", "11")
        self._claude_session("e5f6a7b8", "12")
        # 诱饵：另一个项目的历史，不能算作本项目的
        self._claude_session("zzzz9999", "12", cwd="/somewhere/else")
        r = self.run_probe()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude-code", r.stdout)
        self.assertIn("a1b2c3d4", r.stdout)
        self.assertIn("e5f6a7b8", r.stdout)
        self.assertIn("2 个会话", r.stdout)
        self.assertNotIn("zzzz9999", r.stdout)
        self.assertIn("不读消息正文", r.stdout)
        self.assertNotIn("消息正文，不许", r.stdout)   # 隐私红线：正文绝不出现

    def test_probe_cwd_fallback_when_encoding_differs(self) -> None:
        # 目录名编码猜不中的情况下，靠首行 cwd 精确匹配仍然能归属
        self._claude_session("cafe1234", "12", cwd="/somewhere/else")
        d = self.home / ".claude" / "projects" / "weird-encoding-xyz"
        d.mkdir(parents=True)
        (d / "cafe1234.jsonl").write_text(
            '{"sessionId":"cafe1234","cwd":%s,"timestamp":"2026-10-12T10:00:00.000Z"}\n'
            % json.dumps(str(self.tmp)), encoding="utf-8")
        r = self.run_probe()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cafe1234", r.stdout)
        self.assertIn("1 个会话", r.stdout)

    def test_probe_counts_aider_turns_in_project(self) -> None:
        (self.tmp / ".aider.chat.history.md").write_text(
            "# aider chat started\n#### 把登录改成手机号\nsome reply\n"
            "#### 再加个验证码\nmore reply\n", encoding="utf-8")
        r = self.run_probe()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("aider", r.stdout)
        self.assertIn("2 轮", r.stdout)

    def test_probe_empty_home_is_graceful(self) -> None:
        r = self.run_probe()
        self.assertEqual(r.returncode, 0)
        self.assertIn("未发现", r.stdout)

    def test_plan_surfaces_probe_findings(self) -> None:
        self._claude_session("a1b2c3d4", "11")
        env = _isolated_env(self.home)
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "retrofit.py"), str(self.tmp), "plan"],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("本机探测到以下工具的会话存储", r.stdout)
        self.assertIn("a1b2c3d4", r.stdout)
        self.assertIn("git 画像名本身就是稳定标签", r.stdout)

    # ---- doctor 集成：本机会话证据驱动指针补齐 ----

    def run_doctor(self, *extra: str) -> subprocess.CompletedProcess:
        env = _isolated_env(self.home)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "doctor.py"), str(self.tmp), *extra],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )

    def test_doctor_flags_local_tool_without_pointer(self) -> None:
        init_project.init(self.tmp, "detected", False)
        self._claude_session("a1b2c3d4", "11")
        r = self.run_doctor()
        self.assertTrue(r.stdout, r.stderr)
        self.assertIn("session-stores", r.stdout)
        self.assertIn("本机在用", r.stdout)
        self.assertIn("claude-code->CLAUDE.md", r.stdout)
        self.assertIn("sync_rules.py --all", r.stdout)
        self.assertNotIn("不许被探测读走", r.stdout)   # 隐私红线：正文绝不出现

    def test_doctor_flags_unscoped_local_store_too(self) -> None:
        # 全局存储（未确认归属本项目）同样是"本机在用"的实证，一样要报
        init_project.init(self.tmp, "detected", False)
        (self.tmp / ".aider.chat.history.md").write_text(
            "# aider chat started\n#### 把登录改成手机号\nsome reply\n", encoding="utf-8")
        r = self.run_doctor()
        self.assertIn("aider->CONVENTIONS.md", r.stdout)

    def test_doctor_fix_writes_pointer_for_local_tool(self) -> None:
        init_project.init(self.tmp, "detected", False)
        self._claude_session("a1b2c3d4", "11")
        r = self.run_doctor("--fix")
        self.assertTrue(r.stdout, r.stderr)
        self.assertTrue((self.tmp / "CLAUDE.md").is_file(), "--fix 必须补指针")
        self.assertIn("已按本地会话证据补指针", r.stdout)

    def test_doctor_silent_when_no_local_stores(self) -> None:
        init_project.init(self.tmp, "detected", False)
        r = self.run_doctor()
        self.assertIn("session-stores", r.stdout)
        self.assertIn("未发现", r.stdout)


# --------------------------------------------------------------- ADR 检查

class TestDecisions(ProjectFixture):
    """check_decisions：编号唯一 + 文件名合规（审计现场：两个 0005）。"""

    def _adr(self, name: str, body: str = "# ADR\n\n## 背景\nx\n") -> Path:
        p = self.ai / "decisions" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def test_duplicate_number_is_error(self) -> None:
        self._adr("0005-codex-breach-网关回调签名.md")
        self._adr("0005-社区与计费架构方案.md")
        self._adr("0004-设备证明存储选型.md")
        findings = doctor.run_doctor(self.tmp, False)
        dup = [f for f in findings if f.area == "decisions" and "duplicate" in f.msg]
        self.assertTrue(dup, "编号撞号必须报 err")
        self.assertEqual(dup[0].level, "err")
        self.assertIn("#0005", dup[0].msg)
        self.assertIn("0005-codex-breach", dup[0].msg)
        self.assertIn("0005-社区与计费架构方案.md", dup[0].msg)

    def test_bad_filename_is_warn(self) -> None:
        self._adr("0004-设备证明存储选型.md")
        self._adr("随手记.md")
        findings = doctor.run_doctor(self.tmp, False)
        bad = [f for f in findings if f.area == "decisions" and "不合规" in f.msg]
        self.assertTrue(bad, "非 NNNN-主题.md 文件名必须报 warn")
        self.assertEqual(bad[0].level, "warn")
        self.assertIn("随手记.md", bad[0].msg)

    def test_unique_numbers_are_ok(self) -> None:
        self._adr("0004-设备证明存储选型.md")
        self._adr("0005-codex-breach-网关回调签名.md")
        findings = doctor.run_doctor(self.tmp, False)
        ok = [f for f in findings if f.area == "decisions" and "unique" in f.msg]
        self.assertTrue(ok, "编号唯一时应报 ok")
        self.assertEqual(ok[0].level, "ok")

    def test_no_decisions_dir_is_ok(self) -> None:
        findings = doctor.run_doctor(self.tmp, False)
        msgs = [f.msg for f in findings if f.area == "decisions"]
        # init 已建空 decisions/ 目录 → "empty (no ADR yet)"；目录整个没了也是同类 ok
        self.assertTrue(msgs and all("no ADR yet" in m for m in msgs), msgs)
        self.assertTrue(all(f.level == "ok" for f in findings if f.area == "decisions"))


# --------------------------------------------------------------- stdio 安全

class TestStdioSafety(unittest.TestCase):
    """Windows GBK 控制台打印崩栈的回归测试（审计现场目击）。

    崩因：print 回声项目数据时碰到控制台编码编不出的字符（⚠ ✓ emoji 生僻字），
    strict 策略直接 UnicodeEncodeError。ensure_stdio_safe() 把策略换成 replace：
    编不出的降级成 `?`，当前编码（GBK）下的中文依旧全对。
    """

    def _run_gbk(self, use_fix: bool) -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp(prefix="devctx-gbk-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        script = tmp / "echo_unicode.py"
        script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            + ("import _common\n_common.ensure_stdio_safe()\n" if use_fix else "")
            + "print('\u26a0 编不出的字符')\n"
            + "print('中文项目数据')\n",
            encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="gbk")
        # 按字节收：子进程用 GBK 编码输出，中文以 GBK 字节流原样到达
        return subprocess.run([sys.executable, str(script)],
                              capture_output=True, env=env)

    def test_gbk_console_crashes_without_the_fix(self) -> None:
        r = self._run_gbk(use_fix=False)
        self.assertNotEqual(r.returncode, 0, "未修复时 GBK 控制台必须崩（锁住崩因）")
        self.assertIn(b"UnicodeEncodeError", r.stderr)

    def test_gbk_console_survives_with_the_fix(self) -> None:
        r = self._run_gbk(use_fix=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"?", r.stdout, "编不出的字符降级为 ?")
        self.assertIn("中文项目数据".encode("gbk"), r.stdout,
                      "GBK 可编码的中文按原编码全对输出")
        self.assertEqual(r.stderr, b"", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)