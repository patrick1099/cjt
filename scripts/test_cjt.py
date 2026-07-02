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


if __name__ == "__main__":
    unittest.main()
