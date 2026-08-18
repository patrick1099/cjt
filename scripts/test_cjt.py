# 用途: cjt.py 的 stdlib unittest; 运行: cd scripts; py -3 -m unittest test_cjt -v
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import cjt


class TestParseRef(unittest.TestCase):
    def test_strong_forms(self):
        r = cjt.parse_ref("App/Code/main.c:123")
        self.assertEqual((r.path, r.line, r.end, r.strong),
                         ("App/Code/main.c", 123, None, True))
        r = cjt.parse_ref("App\\Code\\main.c:123-145")
        self.assertEqual((r.path, r.line, r.end, r.strong),
                         ("App\\Code\\main.c", 123, 145, True))
        r = cjt.parse_ref("C:\\x\\y.c:3")
        self.assertEqual((r.path, r.line, r.strong), ("C:\\x\\y.c", 3, True))
        r = cjt.parse_ref("docs/需求规格/说明.md:9")
        self.assertEqual((r.path, r.strong), ("docs/需求规格/说明.md", True))

    def test_weak_forms(self):
        r = cjt.parse_ref("main.c:7")
        self.assertEqual((r.path, r.line, r.strong), ("main.c", 7, False))
        r = cjt.parse_ref("Makefile:12")
        self.assertEqual((r.path, r.strong), ("Makefile", False))
        r = cjt.parse_ref("12:30")
        self.assertEqual((r.path, r.strong), ("12", False))

    def test_rejects(self):
        self.assertIsNone(cjt.parse_ref("http://x:80"))          # URL scheme
        self.assertIsNone(cjt.parse_ref("vscode://a.b/goto"))    # 无 :line 也拒
        self.assertIsNone(cjt.parse_ref("a/b.c:0"))              # 行号 < 1
        self.assertIsNone(cjt.parse_ref("没有冒号数字"))
        self.assertIsNone(cjt.parse_ref("a/b.c"))                # 无行号

    def test_raw_keeps_original(self):
        self.assertEqual(cjt.parse_ref("a/b.c:12-15").raw, "a/b.c:12-15")


class TestPattern(unittest.TestCase):
    def test_ts_escape_specials(self):
        self.assertEqual(
            cjt.ts_escape("a.b*c+d?e^f$g{h}i(j)k|l[m]n\\o"),
            "a\\.b\\*c\\+d\\?e\\^f\\$g\\{h\\}i\\(j\\)k\\|l\\[m\\]n\\\\o")

    def test_ts_escape_leaves_nonspecials(self):
        # 这些不在 TS 集合内, 决不能转义(re.escape 会——所以禁用 re.escape)
        self.assertEqual(cjt.ts_escape("a-b/c~d'e\"f g,h;i"), "a-b/c~d'e\"f g,h;i")

    def test_line_pattern(self):
        self.assertIsNone(cjt.line_pattern("   \t  "))
        self.assertIsNone(cjt.line_pattern(""))
        self.assertEqual(cjt.line_pattern("  int x;  "), "^[^\\S\\n]*int x;")
        self.assertEqual(cjt.line_pattern("你好 world"), "^[^\\S\\n]*你好 world")


class TestFormQuote(unittest.TestCase):
    def test_whatwg_parity(self):
        self.assertEqual(cjt.form_quote("a b"), "a+b")
        self.assertEqual(cjt.form_quote("*-._"), "*-._")   # WHATWG 保留集
        self.assertEqual(cjt.form_quote("~"), "%7E")        # JS 转义 ~ (Py quote_plus 不转义)
        self.assertEqual(cjt.form_quote("/"), "%2F")
        self.assertEqual(cjt.form_quote("\\"), "%5C")
        self.assertEqual(cjt.form_quote("中"), "%E4%B8%AD")  # UTF-8 逐字节, 大写十六进制


class TestBuildUrl(unittest.TestCase):
    def test_golden(self):
        # 金样: 按扩展 tagLinkMarkdown()+URLSearchParams 规则手工推导的字节级结果
        url = cjt.build_url("App/Code/main.c", 123, "  if (a*b) { /* hi */ }")
        self.assertEqual(
            url,
            "vscode://patrick1099.code-jump-tags/goto?file=App%2FCode%2Fmain.c&line=123"
            "&pattern=%5E%5B%5E%5CS%5Cn%5D*if+%5C%28a%5C*b%5C%29+%5C%7B+%2F%5C*+hi+%5C*%2F+%5C%7D")

    def test_blank_line_no_pattern(self):
        self.assertEqual(cjt.build_url("a.c", 5, "   "),
                         "vscode://patrick1099.code-jump-tags/goto?file=a.c&line=5")


def _mock_resolver(files):
    """files: {rel_posix: [行...]}; 模拟 Port, 测试专用。"""
    def r(path):
        key = path.replace("\\", "/")
        if key.startswith("OUT/"):
            return ("outside-root", None)
        if key not in files:
            return ("file-not-found", None)
        return ("ok", (key, files[key]))
    return r


FILES = {
    "App/main.c": ["#include <a.h>", "int main(void)", "{", "    return 0;", "}"],
    "Makefile": ["all: build"],
}


class TestConvertText(unittest.TestCase):
    def conv(self, text):
        return cjt.convert_text(text, _mock_resolver(FILES))

    def url(self, rel, line):
        return cjt.build_url(rel, line, FILES[rel][line - 1])

    def test_bare(self):
        out, n, misses = self.conv("見 App/main.c:2 一行\n")
        self.assertEqual(out, "見 [App/main.c:2](%s) 一行\n" % self.url("App/main.c", 2))
        self.assertEqual((n, misses), (1, []))

    def test_inline_code(self):
        out, n, _ = self.conv("看 `App/main.c:2` 这里\n")
        self.assertEqual(out, "看 [`App/main.c:2`](%s) 这里\n" % self.url("App/main.c", 2))
        self.assertEqual(n, 1)

    def test_md_link_label_kept(self):
        out, n, _ = self.conv("[主循环入口](App/main.c:2)\n")
        self.assertEqual(out, "[主循环入口](%s)\n" % self.url("App/main.c", 2))
        self.assertEqual(n, 1)

    def test_range_jumps_to_start_label_keeps_range(self):
        out, n, _ = self.conv("`App/main.c:2-4`\n")
        self.assertEqual(out, "[`App/main.c:2-4`](%s)\n" % self.url("App/main.c", 2))

    def test_backslash_path(self):
        out, n, _ = self.conv("App\\main.c:2\n")
        self.assertEqual(out, "[App\\main.c:2](%s)\n" % self.url("App/main.c", 2))

    def test_fenced_block_skipped(self):
        text = "```c\nApp/main.c:2\n```\n~~~\n`App/main.c:2`\n~~~\n"
        out, n, misses = self.conv(text)
        self.assertEqual(out, text)
        self.assertEqual((n, misses), (0, []))

    def test_four_backtick_fence_not_closed_by_triple(self):
        text = "````\n```\nApp/main.c:2\n````\n"
        out, n, misses = self.conv(text)
        self.assertEqual(out, text)
        self.assertEqual((n, misses), (0, []))

    def test_idempotent(self):
        once, n1, _ = self.conv("`App/main.c:2`\n")
        twice, n2, misses = self.conv(once)
        self.assertEqual(twice, once)
        self.assertEqual((n2, misses), (0, []))

    def test_strong_miss_reported(self):
        text = "坏引用 App/gone.c:9 在此\n第二行 OUT/x.c:1\n"
        out, n, misses = self.conv(text)
        self.assertEqual(out, text)                      # 原文保留
        self.assertEqual(n, 0)
        self.assertEqual(misses, [
            {"ref": "App/gone.c:9", "line_in_doc": 1, "reason": "file-not-found"},
            {"ref": "OUT/x.c:1", "line_in_doc": 2, "reason": "outside-root"},
        ])

    def test_line_out_of_range_miss(self):
        out, n, misses = self.conv("`App/main.c:99`\n")
        self.assertEqual(n, 0)
        self.assertEqual(misses[0]["reason"], "line-out-of-range")
        self.assertEqual(misses[0]["ref"], "App/main.c:99")

    def test_weak_nonexistent_silent(self):
        text = "时间 12:30, 版本 v1.2:3, 比例 4:5\n"
        out, n, misses = self.conv(text)
        self.assertEqual(out, text)
        self.assertEqual((n, misses), (0, []))           # 弱候选不报 miss

    def test_weak_existing_converted(self):
        out, n, _ = self.conv("Makefile:1\n")
        self.assertEqual(out, "[Makefile:1](%s)\n" % self.url("Makefile", 1))

    def test_url_with_port_untouched(self):
        text = "http://x:80/a 和 https://e.com:443\n"
        out, n, misses = self.conv(text)
        self.assertEqual(out, text)
        self.assertEqual((n, misses), (0, []))

    def test_multiple_refs_one_line(self):
        out, n, _ = self.conv("`App/main.c:1` 与 App/main.c:3\n")
        self.assertEqual(n, 2)
        self.assertIn("[`App/main.c:1`](", out)
        self.assertIn("[App/main.c:3](", out)

    def test_blank_source_line_no_pattern(self):
        files = {"a/b.c": ["int x;", "   "]}
        out, n, _ = cjt.convert_text("`a/b.c:2`\n", _mock_resolver(files))
        self.assertIn("goto?file=a%2Fb.c&line=2)", out)  # 无 pattern 参数

    def test_crlf_preserved(self):
        out, n, _ = self.conv("`App/main.c:2`\r\n下一行\r\n")
        self.assertTrue(out.endswith(")\r\n下一行\r\n"))


class TestFsResolver(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        src = os.path.join(self.root, "src")
        os.makedirs(src)
        with open(os.path.join(src, "u.c"), "w", encoding="utf-8", newline="") as f:
            f.write("int a;\n// 注释A\n")
        with open(os.path.join(src, "g.c"), "wb") as f:
            f.write("// 中文注释\nint b;\n".encode("cp936"))
        with open(os.path.join(src, "bad.bin"), "wb") as f:
            f.write(b"text\x81\x00tail")           # utf-8 与 cp936 都解不了

    def test_utf8_ok(self):
        r = cjt.make_fs_resolver(self.root, None)
        status, (rel, lines) = r("src/u.c")
        self.assertEqual((status, rel), ("ok", "src/u.c"))
        self.assertEqual(lines[1], "// 注释A")

    def test_cp936_fallback(self):
        r = cjt.make_fs_resolver(self.root, None)
        status, (rel, lines) = r("src\\g.c")       # 反斜杠输入也可
        self.assertEqual((status, rel), ("ok", "src/g.c"))
        self.assertEqual(lines[0], "// 中文注释")

    def test_encoding_override(self):
        r = cjt.make_fs_resolver(self.root, "cp936")
        status, (rel, lines) = r("src/g.c")
        self.assertEqual(lines[0], "// 中文注释")

    def test_abs_path_relativized(self):
        r = cjt.make_fs_resolver(self.root, None)
        status, (rel, _) = r(os.path.join(self.root, "src", "u.c"))
        self.assertEqual((status, rel), ("ok", "src/u.c"))

    def test_outside_root(self):
        r = cjt.make_fs_resolver(os.path.join(self.root, "src"), None)
        status, payload = r(os.path.join(self.root, "elsewhere.c"))
        self.assertEqual((status, payload), ("outside-root", None))

    def test_file_not_found(self):
        r = cjt.make_fs_resolver(self.root, None)
        self.assertEqual(r("src/nope.c"), ("file-not-found", None))

    def test_decode_error(self):
        r = cjt.make_fs_resolver(self.root, None)
        self.assertEqual(r("src/bad.bin"), ("decode-error", None))


class TestFindRoot(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)

    def test_git_dir(self):
        os.makedirs(os.path.join(self.top, ".git"))
        deep = os.path.join(self.top, "a", "b")
        os.makedirs(deep)
        self.assertEqual(cjt.find_root(deep), self.top)

    def test_git_file_worktree(self):
        with open(os.path.join(self.top, ".git"), "w") as f:
            f.write("gitdir: elsewhere\n")
        self.assertEqual(cjt.find_root(self.top), self.top)

    def test_not_found(self):
        # 临时目录的祖先是否有 .git 不受测试控制, 只验证类型契约
        result = cjt.find_root(self.top)
        self.assertTrue(result is None or isinstance(result, str))


def _run_cli(*argv):
    """直调 main, 捕获 stdout/stderr 与退出码。"""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cjt.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "a.c"), "w",
                  encoding="utf-8", newline="") as f:
            f.write("int x;\nint y;\n")
        self.doc = os.path.join(self.root, "doc.md")
        with open(self.doc, "w", encoding="utf-8", newline="") as f:
            f.write("見 `src/a.c:2` 与 src/gone.c:1\n")

    def read_doc(self):
        with open(self.doc, "r", encoding="utf-8", newline="") as f:
            return f.read()

    def test_convert_json(self):
        code, out, _ = _run_cli("convert", self.doc, "--root", self.root,
                                "--format", "json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["converted"], 1)
        self.assertEqual(rep["misses"][0]["ref"], "src/gone.c:1")
        self.assertEqual(rep["misses"][0]["reason"], "file-not-found")
        self.assertFalse(rep["dry_run"])
        expected_url = cjt.build_url("src/a.c", 2, "int y;")
        self.assertIn("[`src/a.c:2`](%s)" % expected_url, self.read_doc())

    def test_dry_run_leaves_file(self):
        before = self.read_doc()
        code, out, _ = _run_cli("convert", self.doc, "--root", self.root,
                                "--dry-run", "--format", "json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["dry_run"])
        self.assertEqual(self.read_doc(), before)

    def test_convert_failed_write_leaves_original(self):
        before = self.read_doc()
        real_replace = os.replace

        def boom(*a, **kw):
            raise OSError("模拟写入失败")

        os.replace = boom
        try:
            code, _, err = _run_cli("convert", self.doc, "--root", self.root,
                                    "--format", "json")
        finally:
            os.replace = real_replace
        self.assertEqual(code, 1)
        self.assertIn("写入失败", err)
        self.assertEqual(self.read_doc(), before)

    def test_convert_missing_doc_exits_1(self):
        code, _, err = _run_cli("convert", os.path.join(self.root, "nope.md"),
                                "--root", self.root)
        self.assertEqual(code, 1)
        self.assertIn("nope.md", err)

    def test_link_json(self):
        code, out, _ = _run_cli("link", "src/a.c:2", "--root", self.root,
                                "--format", "json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["url"], cjt.build_url("src/a.c", 2, "int y;"))
        self.assertEqual(rep["markdown"], "[`src/a.c:2`](%s)" % rep["url"])

    def test_link_custom_label(self):
        code, out, _ = _run_cli("link", "src/a.c:2", "第二行", "--root", self.root)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("[第二行](vscode://"))

    def test_link_bad_ref_exits_1(self):
        code, _, err = _run_cli("link", "http://x:80", "--root", self.root)
        self.assertEqual(code, 1)

    def test_link_out_of_range_exits_1(self):
        code, _, err = _run_cli("link", "src/a.c:99", "--root", self.root)
        self.assertEqual(code, 1)
        self.assertIn("line-out-of-range", err)


def _fake_id_gen():
    n = [0]
    def gen(prefix):
        n[0] += 1
        return "%s_test%d" % (prefix, n[0])
    return gen


FAKE_NOW = lambda: "2026-07-02T00:00:00.000Z"


def _ref(file, line, note="n", pattern="auto"):
    if pattern == "auto":
        pattern = "^[^\\S\\n]*line%d" % line
    return {"note": note, "file": file, "line": line, "pattern": pattern}


class TestExtractGotoRefs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.URL2 = cjt.build_url("App/main.c", 2, "int main(void)")

    def test_basic_extract_and_decode(self):
        refs = cjt.extract_goto_refs("[主循环](%s)\n" % self.URL2)
        self.assertEqual(refs, [{
            "note": "主循环", "file": "App/main.c", "line": 2,
            "pattern": "^[^\\S\\n]*int main\\(void\\)"}])

    def test_chinese_and_space_decode(self):
        url = cjt.build_url("docs/需求.md", 1, "  a b 中文  ")
        refs = cjt.extract_goto_refs("[x](%s)\n" % url)
        self.assertEqual(refs[0]["pattern"], "^[^\\S\\n]*a b 中文")
        self.assertEqual(refs[0]["file"], "docs/需求.md")

    def test_no_pattern_link(self):
        url = cjt.build_url("a.c", 5, "   ")   # 空白行无 pattern
        refs = cjt.extract_goto_refs("[x](%s)\n" % url)
        self.assertEqual(refs[0]["pattern"], None)

    def test_fence_skipped(self):
        text = "```\n[x](%s)\n```\n" % self.URL2
        self.assertEqual(cjt.extract_goto_refs(text), [])

    def test_foreign_links_ignored(self):
        text = ("[a](vscode://other.ext/goto?file=x&line=1)\n"
                "[b](https://e.com/x)\n"
                "[c](vscode://patrick1099.code-jump-tags/other?file=x&line=1)\n")
        self.assertEqual(cjt.extract_goto_refs(text), [])

    def test_dedupe_first_label_wins(self):
        text = "[甲](%s)\n[乙](%s)\n" % (self.URL2, self.URL2)
        refs = cjt.extract_goto_refs(text)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["note"], "甲")

    def test_order_preserved(self):
        u1 = cjt.build_url("a.c", 1, "x1")
        u3 = cjt.build_url("a.c", 3, "x3")
        refs = cjt.extract_goto_refs("[一](%s) 然后 [三](%s)\n" % (u1, u3))
        self.assertEqual([r["line"] for r in refs], [1, 3])

    def test_bad_line_ignored(self):
        text = "[x](vscode://patrick1099.code-jump-tags/goto?file=a.c&line=abc)\n"
        self.assertEqual(cjt.extract_goto_refs(text), [])


class TestPatternToText(unittest.TestCase):
    def test_roundtrip_with_line_pattern(self):
        for line in ["int x;", "if (a*b) { /* hi */ }", "  你好 world  "]:
            self.assertEqual(cjt.pattern_to_text(cjt.line_pattern(line)),
                             line.strip())

    def test_non_matching_prefix(self):
        self.assertIsNone(cjt.pattern_to_text("no-prefix"))
        self.assertIsNone(cjt.pattern_to_text(None))


class TestSyncDocFolder(unittest.TestCase):
    def test_first_create_at_root_end(self):
        store = {"version": 1, "tree": [{"type": "folder", "id": "f_x",
                                         "title": "已有", "children": []}]}
        rep = cjt.sync_doc_folder(store, "docs/a.md", "教程",
                                  [_ref("a.c", 1, "甲")], _fake_id_gen(), FAKE_NOW)
        self.assertEqual(rep, {"folder": "教程", "added": 1,
                               "updated": 0, "removed": 0})
        folder = store["tree"][-1]                  # 根级末尾, 不挤 inbox 顶部位
        self.assertEqual((folder["type"], folder["title"], folder["source"]),
                         ("folder", "教程", "docs/a.md"))
        tag = folder["children"][0]
        self.assertEqual((tag["type"], tag["note"], tag["file"], tag["line"]),
                         ("tag", "甲", "a.c", 1))
        self.assertEqual(tag["text"], "line1")      # pattern 反解
        self.assertEqual(tag["original"], "line1")
        self.assertEqual(tag["createdAt"], "2026-07-02T00:00:00.000Z")

    def test_resync_add_update_remove_keep_identity(self):
        store = {"version": 1, "tree": []}
        gen = _fake_id_gen()
        cjt.sync_doc_folder(store, "d.md", "d.md",
                            [_ref("a.c", 1, "旧名"), _ref("a.c", 2)], gen, FAKE_NOW)
        folder = store["tree"][0]
        keep_id = folder["children"][0]["id"]
        keep_created = folder["children"][0]["createdAt"]
        rep = cjt.sync_doc_folder(store, "d.md", "d.md",
                                  [_ref("a.c", 1, "新名"), _ref("b.c", 9)],
                                  gen, lambda: "2027-01-01T00:00:00.000Z")
        self.assertEqual(rep, {"folder": "d.md", "added": 1,
                               "updated": 1, "removed": 1})
        kept = folder["children"][0]
        self.assertEqual((kept["id"], kept["createdAt"], kept["note"]),
                         (keep_id, keep_created, "新名"))       # id/createdAt 保持
        self.assertEqual(folder["children"][1]["file"], "b.c")  # 顺序 = 文档序

    def test_rename_keeps_identity(self):
        store = {"version": 1, "tree": []}
        gen = _fake_id_gen()
        cjt.sync_doc_folder(store, "d.md", "d.md", [_ref("a.c", 1)], gen, FAKE_NOW)
        fid = store["tree"][0]["id"]
        cjt.sync_doc_folder(store, "d.md", "新标题", [_ref("a.c", 1)], gen, FAKE_NOW)
        self.assertEqual(len(store["tree"]), 1)                 # 没新建 folder
        self.assertEqual((store["tree"][0]["id"], store["tree"][0]["title"]),
                         (fid, "新标题"))

    def test_non_tag_children_preserved(self):
        store = {"version": 1, "tree": [{
            "type": "folder", "id": "f_1", "title": "d.md", "source": "d.md",
            "children": [{"type": "folder", "id": "f_sub", "title": "手工子夹",
                          "children": []}]}]}
        cjt.sync_doc_folder(store, "d.md", "d.md", [_ref("a.c", 1)],
                            _fake_id_gen(), FAKE_NOW)
        kinds = [c["type"] for c in store["tree"][0]["children"]]
        self.assertEqual(kinds, ["tag", "folder"])              # tag 前, 非 tag 保留在后

    def test_no_pattern_ref_omits_anchor_fields(self):
        store = {"version": 1, "tree": []}
        cjt.sync_doc_folder(store, "d.md", "d.md",
                            [_ref("a.c", 5, pattern=None)], _fake_id_gen(), FAKE_NOW)
        tag = store["tree"][0]["children"][0]
        self.assertNotIn("pattern", tag)
        self.assertNotIn("text", tag)
        self.assertNotIn("original", tag)


class TestUpsertInboxTag(unittest.TestCase):
    def test_lazy_create_inbox_at_top(self):
        store = {"version": 1, "tree": [{"type": "folder", "id": "f_x",
                                         "title": "别的", "children": []}]}
        action = cjt.upsert_inbox_tag(store, _ref("a.c", 1), _fake_id_gen(), FAKE_NOW)
        self.assertEqual(action, "added")
        inbox = store["tree"][0]                    # 顶部
        self.assertEqual((inbox["title"], inbox["inbox"]), ("未分组", True))
        self.assertEqual(inbox["children"][0]["file"], "a.c")

    def test_reuse_existing_inbox(self):
        store = {"version": 1, "tree": [{"type": "folder", "id": "f_i",
                                         "title": "未分组", "inbox": True,
                                         "children": []}]}
        cjt.upsert_inbox_tag(store, _ref("a.c", 1), _fake_id_gen(), FAKE_NOW)
        self.assertEqual(len(store["tree"]), 1)

    def test_existing_location_updates_anywhere(self):
        store = {"version": 1, "tree": [{
            "type": "folder", "id": "f_1", "title": "夹", "children": [
                {"type": "tag", "id": "t_old", "note": "旧", "file": "a.c",
                 "line": 1, "createdAt": "x"}]}]}
        action = cjt.upsert_inbox_tag(store, _ref("a.c", 1, "新"),
                                      _fake_id_gen(), FAKE_NOW)
        self.assertEqual(action, "updated")
        self.assertEqual(store["tree"][0]["children"][0]["note"], "新")
        self.assertEqual(len(store["tree"]), 1)     # 没建 inbox


class TestSerializeStore(unittest.TestCase):
    def test_matches_js_stringify(self):
        store = {"version": 1, "tree": [{"type": "folder", "id": "f_1",
                                         "title": "文档", "children": []}]}
        expected = (
            '{\n'
            '  "version": 1,\n'
            '  "tree": [\n'
            '    {\n'
            '      "type": "folder",\n'
            '      "id": "f_1",\n'
            '      "title": "文档",\n'
            '      "children": []\n'
            '    }\n'
            '  ]\n'
            '}')
        self.assertEqual(cjt.serialize_store(store), expected)

    def test_no_unicode_escape_no_trailing_newline(self):
        out = cjt.serialize_store({"version": 1, "tree": []})
        self.assertFalse(out.endswith("\n"))
        out2 = cjt.serialize_store({"version": 1, "tree": [
            {"type": "folder", "id": "f", "title": "中文", "children": []}]})
        self.assertIn('"中文"', out2)
        self.assertNotIn("\\u", out2)


class TestBase36(unittest.TestCase):
    def test_values(self):
        self.assertEqual(cjt.base36(0), "0")
        self.assertEqual(cjt.base36(35), "z")
        self.assertEqual(cjt.base36(36), "10")
        # 金样已用 node -e "console.log((1751414400000).toString(36))" 验证
        self.assertEqual(cjt.base36(1751414400000), "mcl6ww00")


class TestStoreIO(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.path = os.path.join(self.root, ".code-jump-tags", "store.json")

    def test_missing_returns_empty(self):
        store, mtime = cjt.load_store(self.root)
        self.assertEqual(store, {"version": 1, "tree": []})
        self.assertIsNone(mtime)

    def test_roundtrip_creates_dir(self):
        store, mtime = cjt.load_store(self.root)
        store["tree"].append({"type": "folder", "id": "f_1", "title": "中文",
                              "children": []})
        self.assertTrue(cjt.save_store(self.root, store, mtime))
        back, mtime2 = cjt.load_store(self.root)
        self.assertEqual(back, store)
        self.assertIsNotNone(mtime2)
        with open(self.path, "rb") as f:
            self.assertNotIn(b"\\u", f.read())      # ensure_ascii=False 落盘

    def test_corrupt_raises(self):
        os.makedirs(os.path.dirname(self.path))
        for bad in ("{not json", '{"version": 2, "tree": []}',
                    '{"version": 1, "tree": "x"}'):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(bad)
            with self.assertRaises(cjt.StoreCorruptError):
                cjt.load_store(self.root)

    def test_mtime_mismatch_refuses(self):
        store, mtime = cjt.load_store(self.root)
        cjt.save_store(self.root, store, mtime)          # 建出文件
        store2, mtime2 = cjt.load_store(self.root)
        os.utime(self.path, (mtime2 + 10, mtime2 + 10))  # 模拟并发写
        self.assertFalse(cjt.save_store(self.root, store2, mtime2))


class TestIdAndTime(unittest.TestCase):
    def test_id_format(self):
        self.assertRegex(cjt.new_node_id("t"), r"^t_[0-9a-z]+_[0-9a-z]{4}$")
        self.assertRegex(cjt.new_node_id("f"), r"^f_[0-9a-z]+_[0-9a-z]{4}$")

    def test_iso_z_format(self):
        self.assertRegex(cjt.now_iso_z(),
                         r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class TestCliTags(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "a.c"), "w",
                  encoding="utf-8", newline="") as f:
            f.write("int x;\nint y;\nint z;\n")
        self.doc = os.path.join(self.root, "doc.md")
        with open(self.doc, "w", encoding="utf-8", newline="") as f:
            f.write("见 `src/a.c:2` 与 `src/a.c:3`\n")
        self.store_path = os.path.join(self.root, ".code-jump-tags",
                                       "store.json")

    def read_store(self):
        with open(self.store_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_convert_with_tags(self):
        code, out, _ = _run_cli("convert", self.doc, "--root", self.root,
                                "--tags", "--name", "冒烟教程",
                                "--format", "json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["tags"], {"folder": "冒烟教程", "added": 2,
                                       "updated": 0, "removed": 0})
        store = self.read_store()
        folder = store["tree"][-1]
        self.assertEqual((folder["title"], folder["source"]),
                         ("冒烟教程", "doc.md"))
        self.assertEqual([(t["file"], t["line"]) for t in folder["children"]],
                         [("src/a.c", 2), ("src/a.c", 3)])
        self.assertEqual(folder["children"][0]["text"], "int y;")

    def test_convert_without_tags_untouched(self):
        code, out, _ = _run_cli("convert", self.doc, "--root", self.root,
                                "--format", "json")
        self.assertEqual(code, 0)
        self.assertNotIn("tags", json.loads(out))
        self.assertFalse(os.path.exists(self.store_path))

    def test_dry_run_skips_store(self):
        code, out, _ = _run_cli("convert", self.doc, "--root", self.root,
                                "--tags", "--dry-run", "--format", "json")
        self.assertEqual(code, 0)
        self.assertNotIn("tags", json.loads(out))
        self.assertFalse(os.path.exists(self.store_path))

    def test_tags_subcommand_rerun_sync(self):
        _run_cli("convert", self.doc, "--root", self.root)      # 先转换
        code, out, _ = _run_cli("tags", self.doc, "--root", self.root,
                                "--format", "json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["tags"]["added"], 2)
        self.assertEqual(rep["tags"]["folder"], "doc.md")       # 缺省标题=source
        store = self.read_store()
        keep_id = store["tree"][-1]["children"][0]["id"]
        # 文档删掉第二条引用后重跑 -> removed=1, 首条 id 保持
        with open(self.doc, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        head, _, _ = text.partition(" 与 ")
        with open(self.doc, "w", encoding="utf-8", newline="") as f:
            f.write(head + "\n")
        code, out, _ = _run_cli("tags", self.doc, "--root", self.root,
                                "--format", "json")
        rep = json.loads(out)
        self.assertEqual((rep["tags"]["added"], rep["tags"]["removed"]), (0, 1))
        store = self.read_store()
        self.assertEqual(len(store["tree"][-1]["children"]), 1)
        self.assertEqual(store["tree"][-1]["children"][0]["id"], keep_id)

    def test_tags_no_links_ok(self):
        plain = os.path.join(self.root, "plain.md")
        with open(plain, "w", encoding="utf-8", newline="") as f:
            f.write("没有链接\n")
        code, out, _ = _run_cli("tags", plain, "--root", self.root,
                                "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["tags"],
                         {"folder": "plain.md", "added": 0,
                          "updated": 0, "removed": 0})

    def test_link_tags_inbox_then_update(self):
        code, out, _ = _run_cli("link", "src/a.c:2", "第二行", "--root",
                                self.root, "--tags", "--format", "json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual(rep["tag"], {"action": "added", "folder": "未分组"})
        store = self.read_store()
        self.assertEqual((store["tree"][0]["title"], store["tree"][0]["inbox"]),
                         ("未分组", True))
        self.assertEqual(store["tree"][0]["children"][0]["note"], "第二行")
        code, out, _ = _run_cli("link", "src/a.c:2", "改名", "--root",
                                self.root, "--tags", "--format", "json")
        rep = json.loads(out)
        self.assertEqual(rep["tag"], {"action": "updated"})
        store = self.read_store()
        self.assertEqual(len(store["tree"][0]["children"]), 1)
        self.assertEqual(store["tree"][0]["children"][0]["note"], "改名")

    def test_link_tags_default_note_plain_ref(self):
        code, out, _ = _run_cli("link", "src/a.c:2", "--root", self.root,
                                "--tags", "--format", "json")
        self.assertEqual(code, 0)
        store = self.read_store()
        self.assertEqual(store["tree"][0]["children"][0]["note"], "src/a.c:2")
        # note 不带反引号(反引号是 markdown 样式不是标签名)

    def test_corrupt_store_refused_untouched(self):
        os.makedirs(os.path.dirname(self.store_path))
        with open(self.store_path, "w", encoding="utf-8") as f:
            f.write("{broken")
        code, _, err = _run_cli("tags", self.doc, "--root", self.root)
        self.assertEqual(code, 1)
        self.assertIn("store", err)
        with open(self.store_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "{broken")               # 原文件未动


if __name__ == "__main__":
    unittest.main()
