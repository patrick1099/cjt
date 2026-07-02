---
name: cjt
description: Convert plain path:line code references in a markdown document into clickable Code Jump Tags vscode:// deep links (work in VS Code and Obsidian). Use AFTER writing any tutorial, report, or doc that cites code locations, when the user asks for jump links / 跳转链接, or mentions code jump tags. Write the doc normally first — do NOT hand-craft vscode:// URLs.
---

# cjt — Code Jump Tags 链接转换

写文档时**正常写引用**，不要手工拼 URL。支持的引用写法（转换时自动识别）：

- `` `App/Code/main.c:123` ``（行内代码，最常用）
- `App/Code/main.c:123` 裸文本 / `App/Code/main.c:123-145` 行区间
- `[自定义标签](App/Code/main.c:123)`
- 裸引用与中文文字之间要留空格（或用行内代码包起来），否则路径会被误粘连

围栏代码块内的引用不会被转换；已是 `vscode://` 的链接不会二次转换（幂等）。

## 用法

文档写完后执行一次：

```
py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/cjt.py convert <doc.md> --format json
```

- 报告中 `misses` 为空 → 完成。
- 有 miss → 按 `reason` 核对该引用（`file-not-found`/`line-out-of-range` 通常是路径或行号写错；路径必须相对**工作区根**），改正文档后重跑。
- 文档在被引用仓库之外（如 Obsidian vault）→ 加 `--root <仓库根>`。
- 只要一条链接：`py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/cjt.py link path:line [标签] --format json`。

## 前提（对人说明，遇到"点了没反应"时提示用户）

- 需已安装 VS Code 扩展 Code Jump Tags（patrick1099.code-jump-tags）。
- 点击时 VS Code 打开的第一个工作区文件夹必须是链接路径的根。
