#!/usr/bin/env python3
# 结构: vibe-scripts/micro
# 用途: cjtag import 之前逐条打印每一步对应的源码行, 人眼核对 note 与代码对得上
# 用法: py -3 check_steps.py <steps.json> [--root 仓库根]
# 原始需求: 单步调试式的链路标签, 行号偏一行就讲错一步; import 本身只校验"行号在范围内", 不校验"是不是那一行"
import argparse
import json
import os
import sys


def read_lines(path):
    """源文件按 UTF-8 解码, 失败回退 CP936(GB2312 固件仓库常见)。"""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return raw.decode("cp936", "replace").splitlines()


def check(steps, root):
    """返回 (报告行列表, 错误数)。纯逻辑, 只读源文件。"""
    out, errors, cache, last_folder = [], 0, {}, None
    for i, s in enumerate(steps):
        folder = s.get("folder") or "(未分组)"
        if folder != last_folder:
            out.append("== " + folder)
            last_folder = folder
        path = os.path.join(root, s["file"])
        if s["file"] not in cache:
            cache[s["file"]] = read_lines(path) if os.path.isfile(path) else None
        lines = cache[s["file"]]
        where = "%s:%d" % (s["file"].split("/")[-1], s["line"])
        if lines is None:
            out.append("  [%d] %s  !! 文件不存在" % (i, where))
            errors += 1
        elif not 1 <= s["line"] <= len(lines):
            out.append("  [%d] %s  !! 行号越界(共 %d 行)" % (i, where, len(lines)))
            errors += 1
        else:
            out.append("  [%d] %s  %s" % (i, where, s["note"][:40]))
            out.append("        | " + lines[s["line"] - 1].strip()[:100])
    return out, errors


def main():
    ap = argparse.ArgumentParser(description="逐条打印 cjtag 清单每一步的源码行")
    ap.add_argument("steps", help="cjtag import 用的 JSON 清单")
    ap.add_argument("--root", default=".", help="工作区根(清单里 file 相对它), 缺省当前目录")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    with open(args.steps, encoding="utf-8") as f:
        steps = json.load(f)
    out, errors = check(steps, args.root)
    print("\n".join(out))
    print("\n共 %d 步, %d 处错误" % (len(steps), errors))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
