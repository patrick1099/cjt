#!/usr/bin/env python3
# 结构: vibe-scripts/standard
# 用途: 把 markdown 文档中的代码引用(path:line)批量转换为 Code Jump Tags 的 vscode:// 跳转链接
# 用法: py -3 cjt.py convert 教程.md --format json  |  py -3 cjt.py link App/Code/main.c:123 主循环
# 原始需求: AI 写教程/文档时正常写 path:line 引用, 写完跑一次 convert 全文变成可点击跳转链接
#           (VS Code/Obsidian 均可点), 无感、零额外 token; pattern 与 URL 编码必须和扩展
#           patrick1099.code-jump-tags 字节级一致。设计: docs/superpowers/specs/2026-07-02-cjt-design.md
import argparse
import functools
import json
import os
import re
import sys
from collections import namedtuple

# ===== 1 配置/常量 =====
EXTENSION_ID = "patrick1099.code-jump-tags"
PATTERN_PREFIX = "^[^\\S\\n]*"          # 与扩展 relocate.ts 的 PATTERN_PREFIX 一致
TS_SPECIALS_RE = re.compile(r"[.*+?^${}()|[\]\\]")  # 与扩展 linePattern() 的转义集一致
SOURCE_ENCODINGS = ("utf-8", "cp936")

# ===== 2 Port: 源文件解析接口 =====
# resolver(path_str: str) -> tuple[str, object]
#   ("ok", (rel_posix: str, lines: list[str]))   成功: root 相对 POSIX 路径 + 全部行
#   ("file-not-found" | "outside-root" | "decode-error", None)
# 行号越界由 Core 依据 lines 长度判定(reason: "line-out-of-range")。

# ===== 3 Core: 纯逻辑(禁止 IO/print) =====
Ref = namedtuple("Ref", "path line end raw strong")

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _ref_core(prefix=""):
    """path:line[-end] 的正则片段; prefix 避免组名在 COMBINED_RE 中冲突。"""
    p = prefix
    return (r"(?P<" + p + r"path>(?:[A-Za-z]:[\\/])?[^\s`\[\]()<>\"']+?)"
            r":(?P<" + p + r"line>[0-9]+)(?:-(?P<" + p + r"end>[0-9]+))?")


REF_FULL_RE = re.compile(_ref_core() + r"\Z")
# 三选一, 按优先级: markdown 链接 > 行内代码 > 裸引用
COMBINED_RE = re.compile(
    r"\[(?P<label>[^\]\n]*)\]\((?P<target>[^)\s]+)\)"
    r"|`(?P<code>[^`\n]+)`"
    r"|" + _ref_core("b"))
FENCE_RE = re.compile(r"[ ]{0,3}(```|~~~)")


def parse_ref(s):
    """把字符串整体解析为 Ref; 不是引用(或是 URL)返回 None。"""
    m = REF_FULL_RE.match(s)
    if not m:
        return None
    path, line = m.group("path"), int(m.group("line"))
    if _SCHEME_RE.match(path) or line < 1:
        return None
    end = m.group("end")
    strong = "/" in path or "\\" in path
    return Ref(path, line, int(end) if end else None, s, strong)


def ts_escape(s):
    return TS_SPECIALS_RE.sub(lambda m: "\\" + m.group(0), s)


def line_pattern(line_text):
    t = line_text.strip()
    return PATTERN_PREFIX + ts_escape(t) if t else None


_FORM_KEEP = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789*-._")


def form_quote(s):
    """WHATWG application/x-www-form-urlencoded 序列化, 与 JS URLSearchParams 字节级一致。"""
    out = []
    for b in s.encode("utf-8"):
        if b in _FORM_KEEP:
            out.append(chr(b))
        elif b == 0x20:
            out.append("+")
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def build_url(rel_path, line, line_text):
    pairs = [("file", rel_path), ("line", str(line))]
    pat = line_pattern(line_text)
    if pat is not None:
        pairs.append(("pattern", pat))
    query = "&".join(k + "=" + form_quote(v) for k, v in pairs)
    return "vscode://" + EXTENSION_ID + "/goto?" + query


def _render_ref(ref, label, lineno, resolver):
    """解析并渲染一个引用 -> (替换文本|None, miss|None)。弱候选失败双 None。"""
    status, payload = resolver(ref.path)
    if status == "ok":
        rel, src_lines = payload
        if ref.line <= len(src_lines):
            url = build_url(rel, ref.line, src_lines[ref.line - 1])
            return "[" + label + "](" + url + ")", None
        status = "line-out-of-range"
    if not ref.strong:
        return None, None
    return None, {"ref": ref.raw, "line_in_doc": lineno, "reason": status}


def _convert_line(line, lineno, resolver):
    out, last, converted, misses = [], 0, 0, []
    for m in COMBINED_RE.finditer(line):
        repl = None
        if m.group("label") is not None:            # [label](target)
            target = m.group("target")
            ref = None if _SCHEME_RE.match(target) else parse_ref(target)
            if ref:
                repl, miss = _render_ref(ref, m.group("label"), lineno, resolver)
                if miss:
                    misses.append(miss)
        elif m.group("code") is not None:           # `code`
            ref = parse_ref(m.group("code").strip())
            if ref:
                repl, miss = _render_ref(ref, "`" + m.group("code") + "`",
                                         lineno, resolver)
                if miss:
                    misses.append(miss)
        else:                                       # 裸引用
            ref = parse_ref(m.group(0))
            if ref:
                repl, miss = _render_ref(ref, m.group(0), lineno, resolver)
                if miss:
                    misses.append(miss)
        if repl is not None:
            out.append(line[last:m.start()])
            out.append(repl)
            last = m.end()
            converted += 1
    out.append(line[last:])
    return "".join(out), converted, misses


def convert_text(text, resolver):
    """全文转换。围栏代码块内不动; 返回 (新全文, 转换数, misses)。"""
    lines = text.splitlines(keepends=True)
    out, converted, misses = [], 0, []
    fence = None
    for lineno, raw in enumerate(lines, 1):
        m = FENCE_RE.match(raw)
        if m and fence is None:
            fence = m.group(1)
            out.append(raw)
            continue
        if fence is not None:
            if m and m.group(1) == fence:
                fence = None
            out.append(raw)
            continue
        new, c, ms = _convert_line(raw, lineno, resolver)
        out.append(new)
        converted += c
        misses.extend(ms)
    return "".join(out), converted, misses

# ===== 4 Adapter: 文件系统 resolver + root 探测 =====
def make_fs_resolver(root, encoding):
    """真实文件系统 resolver。encoding=None 时按 SOURCE_ENCODINGS 依次尝试。"""
    root_abs = os.path.abspath(root)

    @functools.lru_cache(maxsize=256)
    def resolver(path_str):
        if os.path.isabs(path_str) or re.match(r"^[A-Za-z]:[\\/]", path_str):
            full = os.path.abspath(path_str)
        else:
            full = os.path.abspath(os.path.join(root_abs, path_str))
        try:
            rel = os.path.relpath(full, root_abs)
        except ValueError:                      # Windows 跨盘符
            return ("outside-root", None)
        if rel == ".." or rel.startswith(".." + os.sep):
            return ("outside-root", None)
        if not os.path.isfile(full):
            return ("file-not-found", None)
        with open(full, "rb") as f:
            data = f.read()
        for enc in ((encoding,) if encoding else SOURCE_ENCODINGS):
            try:
                text = data.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            return ("decode-error", None)
        return ("ok", (rel.replace("\\", "/"), text.splitlines()))

    return resolver


def find_root(start):
    """从 start 向上找 .git(目录或文件, 兼容 worktree); 找不到返回 None。"""
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent

# ===== 5 App: 命令表 + CLI 入口 =====

if __name__ == "__main__":
    pass
