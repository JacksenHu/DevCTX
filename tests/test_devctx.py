#!/usr/bin/env python3
"""Tests for the per-conversation compression spec and the devctx scripts.

Run with::

    python3 tests/test_devctx.py          # or: python3 -m unittest discover tests

Pure standard library, no network, no third-party deps. Each test builds a
throwaway project in a temp dir so the suite never touches real repos.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import log_handoff  # noqa: E402
import archive_handoff  # noqa: E402
import build_index  # noqa: E402
import doctor  # noqa: E402
import init_project  # noqa: E402
import sync_rules  # noqa: E402
from _common import match_ignore  # noqa: E402


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
                     model: str | None = None) -> Path:
        before = set((self.ai / "handoffs").glob("*.md"))
        path, sid = log_handoff.write_record(
            self.tmp, agent, session, user,
            log_handoff.build_body(done, nxt, verify, risks, None, None, False),
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
            log_handoff.build_body(None, None, None, None, None, None, False)
        # --force 才允许自由格式
        body = log_handoff.build_body(None, None, None, None, "随手记一笔", None, True)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
