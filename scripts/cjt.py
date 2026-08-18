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
import random
import re
import sys
import time
import urllib.parse
from collections import namedtuple
from datetime import datetime, timezone

# ===== 1 配置/常量 =====
EXTENSION_ID = "patrick1099.code-jump-tags"
PATTERN_PREFIX = "^[^\\S\\n]*"          # 与扩展 relocate.ts 的 PATTERN_PREFIX 一致
TS_SPECIALS_RE = re.compile(r"[.*+?^${}()|[\]\\]")  # 与扩展 linePattern() 的转义集一致
SOURCE_ENCODINGS = ("utf-8", "cp936")
STORE_DIRECTORY = ".code-jump-tags"
STORE_FILE = "store.json"
INBOX_TITLE = "\u672a\u5206\u7ec4"   # 与扩展 tree.ts 的 INBOX_TITLE 一致

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
FENCE_RE = re.compile(r"[ ]{0,3}(`{3,}|~{3,})")


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

LINK_RE = re.compile(r"\[(?P<label>[^\]\n]*)\]\((?P<url>vscode://[^)\s]+)\)")
GOTO_PREFIX = "vscode://" + EXTENSION_ID + "/goto?"


def _parse_query(q):
    out = {}
    for pair in q.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k] = urllib.parse.unquote_plus(v)
    return out


def extract_goto_refs(text):
    """从已转换文档提取本扩展 goto 链接。按出现顺序, (file,line) 去重取首个。"""
    refs, seen = [], set()
    fence = None
    for raw in text.splitlines():
        m = FENCE_RE.match(raw)
        if m and fence is None:
            fence = m.group(1)
            continue
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        for lm in LINK_RE.finditer(raw):
            url = lm.group("url")
            if not url.startswith(GOTO_PREFIX):
                continue
            params = _parse_query(url[len(GOTO_PREFIX):])
            file, line = params.get("file"), params.get("line")
            if not file or not line or not line.isdigit():
                continue
            key = (file, int(line))
            if key in seen:
                continue
            seen.add(key)
            refs.append({"note": lm.group("label"), "file": file,
                         "line": int(line), "pattern": params.get("pattern")})
    return refs


def pattern_to_text(pattern):
    """从 line_pattern() 产物反解原始行文本(trim 后); 非本格式返回 None。"""
    if not pattern or not pattern.startswith(PATTERN_PREFIX):
        return None
    body = pattern[len(PATTERN_PREFIX):]
    return re.sub(r"\\([.*+?^${}()|[\]\\])", r"\1", body)


def base36(n):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(digits[r])
    return "".join(reversed(out))


def serialize_store(store):
    """与扩展 serialize() (JSON.stringify(store, null, 2)) 字节一致。"""
    return json.dumps(store, ensure_ascii=False, indent=2)


def make_tag(ref, id_gen, now):
    """按扩展创建字面量的字段顺序构造 tag 节点。"""
    tag = {"type": "tag", "id": id_gen("t"), "note": ref["note"],
           "file": ref["file"], "line": ref["line"]}
    if ref.get("pattern"):
        tag["pattern"] = ref["pattern"]
        t = pattern_to_text(ref["pattern"])
        if t is not None:
            tag["text"] = t
            tag["original"] = t
    tag["createdAt"] = now()
    return tag


def _apply_ref(tag, ref):
    """把 ref 的内容套到既有 tag 上; 返回是否有变化。"""
    changed = False
    if tag.get("note") != ref["note"]:
        tag["note"] = ref["note"]
        changed = True
    pattern = ref.get("pattern")
    if pattern and tag.get("pattern") != pattern:
        tag["pattern"] = pattern
        t = pattern_to_text(pattern)
        if t is not None:
            tag["text"] = t
            tag["original"] = t   # 输入源自文档链接(人审内容), 等同人工重设通道
        changed = True
    return changed


def sync_doc_folder(store, source, title, refs, id_gen, now):
    """按 source 匹配/创建文档 folder, 全量同步其直接子 tag。"""
    folder = None
    for node in store["tree"]:
        if node.get("type") == "folder" and node.get("source") == source:
            folder = node
            break
    if folder is None:
        folder = {"type": "folder", "id": id_gen("f"), "title": title,
                  "source": source, "children": []}
        store["tree"].append(folder)
    else:
        folder["title"] = title
    old = {}
    for child in folder["children"]:
        if child.get("type") == "tag":
            old[(child["file"], child["line"])] = child
    added = updated = 0
    new_tags = []
    for ref in refs:
        existing = old.pop((ref["file"], ref["line"]), None)
        if existing is not None:
            if _apply_ref(existing, ref):
                updated += 1
            new_tags.append(existing)
        else:
            new_tags.append(make_tag(ref, id_gen, now))
            added += 1
    removed = len(old)
    non_tags = [c for c in folder["children"] if c.get("type") != "tag"]
    folder["children"] = new_tags + non_tags
    return {"folder": title, "added": added, "updated": updated,
            "removed": removed}


def _find_tag_by_location(nodes, file, line):
    for node in nodes:
        if (node.get("type") == "tag" and node.get("file") == file
                and node.get("line") == line):
            return node
        if node.get("type") == "folder":
            hit = _find_tag_by_location(node.get("children", []), file, line)
            if hit is not None:
                return hit
    return None


def upsert_inbox_tag(store, ref, id_gen, now):
    """单条 tag: 全树同位已有则更新, 否则进收件箱(无则顶部惰性创建)。"""
    hit = _find_tag_by_location(store["tree"], ref["file"], ref["line"])
    if hit is not None:
        _apply_ref(hit, ref)
        return "updated"
    inbox = None
    for node in store["tree"]:
        if node.get("type") == "folder" and node.get("inbox") is True:
            inbox = node
            break
    if inbox is None:
        inbox = {"type": "folder", "id": id_gen("f"), "title": INBOX_TITLE,
                 "inbox": True, "children": []}
        store["tree"].insert(0, inbox)
    inbox["children"].append(make_tag(ref, id_gen, now))
    return "added"


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
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
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

class StoreCorruptError(Exception):
    pass


def _store_path(root):
    return os.path.join(root, STORE_DIRECTORY, STORE_FILE)


def load_store(root):
    """读 store。不存在 -> (空 store, None); 损坏 -> StoreCorruptError。"""
    path = _store_path(root)
    if not os.path.isfile(path):
        return {"version": 1, "tree": []}, None
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        store = json.loads(raw)
    except ValueError:
        raise StoreCorruptError(path)
    if store.get("version") != 1 or not isinstance(store.get("tree"), list):
        raise StoreCorruptError(path)
    return store, os.path.getmtime(path)


def save_store(root, store, expect_mtime):
    """原子写 store。expect_mtime 与磁盘不符 -> 返回 False 不写(调用方重试)。"""
    path = _store_path(root)
    if (expect_mtime is not None and os.path.isfile(path)
            and os.path.getmtime(path) != expect_mtime):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".cjt-tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(serialize_store(store))
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return True


def new_node_id(prefix):
    """扩展同款 id: <prefix>_<毫秒时间戳36进制>_<4位随机36进制>。"""
    ts = base36(int(time.time() * 1000))
    rand = "".join(random.choice("0123456789abcdefghijklmnopqrstuvwxyz")
                   for _ in range(4))
    return "%s_%s_%s" % (prefix, ts, rand)


def now_iso_z():
    """对齐 JS toISOString(): 毫秒精度 + Z 后缀。"""
    return (datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))

# ===== 5 App: 命令表 + CLI 入口 =====
def _die(msg):
    print("cjt: " + msg, file=sys.stderr)
    sys.exit(1)


def _resolve_root(args):
    root = args.root or find_root(os.getcwd())
    if not root:
        _die("无法从当前目录向上找到 .git; 请用 --root 指定工作区根")
    return root

def _mutate_store(root, mutate):
    """读-改-原子写, 带一次 mtime 竞态重试。mutate(store) -> 报告 dict。"""
    for _ in (0, 1):
        try:
            store, mtime = load_store(root)
        except StoreCorruptError as e:
            _die("store 损坏, 拒绝写入: %s" % e)
        report = mutate(store)
        try:
            if save_store(root, store, mtime):
                return report
        except OSError as e:
            _die("store 写入失败: %s" % e)
    _die("store 并发变更冲突, 重试后仍失败")


def _doc_source(root, doc_path):
    """folder 身份: 文档相对 root 的 POSIX 路径; root 外用绝对 POSIX 路径。"""
    ab = os.path.abspath(doc_path)
    rel = os.path.relpath(ab, os.path.abspath(root))
    if rel == ".." or rel.startswith(".." + os.sep):
        return ab.replace("\\", "/")
    return rel.replace("\\", "/")


def _sync_doc_tags(root, doc_path, doc_text, name):
    refs = extract_goto_refs(doc_text)
    source = _doc_source(root, doc_path)
    title = name or source
    return _mutate_store(root, lambda s: sync_doc_folder(
        s, source, title, refs, new_node_id, now_iso_z))


def cmd_convert(args):
    root = _resolve_root(args)
    try:
        with open(args.doc, "r", encoding="utf-8", newline="") as f:
            text = f.read()
    except FileNotFoundError:
        _die("文档不存在: " + args.doc)
    except UnicodeDecodeError:
        _die("文档不是 UTF-8: " + args.doc)
    resolver = make_fs_resolver(root, args.encoding)
    new_text, converted, misses = convert_text(text, resolver)
    if not args.dry_run and new_text != text:
        tmp = args.doc + ".cjt-tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(new_text)
            os.replace(tmp, args.doc)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            _die("写入失败: %s" % e)
    tags_report = None
    if args.tags and not args.dry_run:
        tags_report = _sync_doc_tags(root, args.doc, new_text, args.name)
    report = {
        "doc": args.doc,
        "root": os.path.abspath(root).replace("\\", "/"),
        "converted": converted,
        "misses": misses,
        "dry_run": args.dry_run,
    }
    if tags_report is not None:
        report["tags"] = tags_report
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False))
    else:
        print("converted %d, misses %d%s" % (
            converted, len(misses), " (dry-run)" if args.dry_run else ""))
        for ms in misses:
            print("  miss %s (doc:%d) %s" % (
                ms["ref"], ms["line_in_doc"], ms["reason"]))
        if tags_report is not None:
            print("tags: folder=%s added=%d updated=%d removed=%d" % (
                tags_report["folder"], tags_report["added"],
                tags_report["updated"], tags_report["removed"]))


def cmd_tags(args):
    root = _resolve_root(args)
    try:
        with open(args.doc, "r", encoding="utf-8", newline="") as f:
            text = f.read()
    except FileNotFoundError:
        _die("文档不存在: " + args.doc)
    except UnicodeDecodeError:
        _die("文档不是 UTF-8: " + args.doc)
    tags_report = _sync_doc_tags(root, args.doc, text, args.name)
    report = {"doc": args.doc,
              "root": os.path.abspath(root).replace("\\", "/"),
              "tags": tags_report}
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False))
    else:
        print("tags: folder=%s added=%d updated=%d removed=%d" % (
            tags_report["folder"], tags_report["added"],
            tags_report["updated"], tags_report["removed"]))


def cmd_link(args):
    root = _resolve_root(args)
    ref = parse_ref(args.ref)
    if not ref:
        _die("无法解析引用: %s (期望 path:line 或 path:line-end)" % args.ref)
    resolver = make_fs_resolver(root, args.encoding)
    status, payload = resolver(ref.path)
    if status != "ok":
        _die("%s: %s" % (status, ref.path))
    rel, src_lines = payload
    if ref.line > len(src_lines):
        _die("line-out-of-range: %s (文件共 %d 行)" % (args.ref, len(src_lines)))
    url = build_url(rel, ref.line, src_lines[ref.line - 1])
    label = args.label if args.label else "`" + args.ref + "`"
    md = "[" + label + "](" + url + ")"
    tag_report = None
    if args.tags:
        note = args.label if args.label else args.ref
        action = _mutate_store(root, lambda s: upsert_inbox_tag(
            s, {"note": note, "file": rel, "line": ref.line,
                "pattern": line_pattern(src_lines[ref.line - 1])},
            new_node_id, now_iso_z))
        tag_report = {"action": action}
        if action == "added":
            tag_report["folder"] = INBOX_TITLE
    if args.format == "json":
        payload = {"markdown": md, "url": url}
        if tag_report is not None:
            payload["tag"] = tag_report
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(md)
        if tag_report is not None:
            print("tag: %s" % tag_report["action"])


COMMANDS = {"convert": cmd_convert, "link": cmd_link, "tags": cmd_tags}


def _add_common(p):
    p.add_argument("--root", help="工作区根(缺省: 从 cwd 向上找 .git)")
    p.add_argument("--encoding", help="源文件编码(缺省: utf-8 失败回退 cp936)")
    p.add_argument("--format", choices=("json", "text"), default="text")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="cjt", description="Code Jump Tags CLI: path:line 引用 -> vscode:// 跳转链接")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("convert", help="原地转换 markdown 文档中的全部代码引用")
    pc.add_argument("doc", help="markdown 文档路径(UTF-8)")
    pc.add_argument("--dry-run", action="store_true", help="只报告, 不写文件")
    pc.add_argument("--tags", action="store_true",
                    help="同步写入 .code-jump-tags 侧边栏标签(dry-run 时跳过)")
    pc.add_argument("--name", help="标签 folder 标题(缺省: 文档相对路径)")
    _add_common(pc)
    pl = sub.add_parser("link", help="生成单条跳转链接")
    pl.add_argument("ref", help="path:line 或 path:line-end")
    pl.add_argument("label", nargs="?", help="链接标签(缺省: `ref` 行内代码样式)")
    pl.add_argument("--tags", action="store_true",
                    help="同时写入侧边栏标签(进「未分组」收件箱)")
    _add_common(pl)
    pt = sub.add_parser("tags", help="把文档中的跳转链接同步为侧边栏标签")
    pt.add_argument("doc", help="已转换的 markdown 文档路径(UTF-8)")
    pt.add_argument("--name", help="标签 folder 标题(缺省: 文档相对路径)")
    pt.add_argument("--root", help="工作区根(缺省: 从 cwd 向上找 .git)")
    pt.add_argument("--format", choices=("json", "text"), default="text")
    args = ap.parse_args(argv)
    COMMANDS[args.cmd](args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")   # 防 GBK 控制台打印异常字符崩溃
        sys.stderr.reconfigure(errors="replace")
    except AttributeError:
        pass
    main()
