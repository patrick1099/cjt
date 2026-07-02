# 用途: cjt.py 的 stdlib unittest; 运行: cd scripts; py -3 -m unittest test_cjt -v
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


if __name__ == "__main__":
    unittest.main()
