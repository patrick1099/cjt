# cjt — Code Jump Tags CLI

把 markdown 文档中的 `path:line` 代码引用批量转换为
[Code Jump Tags](https://marketplace.visualstudio.com/items?itemName=patrick1099.code-jump-tags)
扩展的 `vscode://` 跳转链接。链接自带行内容 pattern，代码漂移后仍可恢复定位。
在 VS Code 和 Obsidian（外部链接）中均可点击跳转。

纯 Python 3 stdlib，无依赖。

## 用法

```
py -3 scripts/cjt.py convert 教程.md [--root DIR] [--dry-run] [--encoding ENC] [--format json|text]
py -3 scripts/cjt.py link App/Code/main.c:123 [标签] [--root DIR] [--format json|text]
```

- `convert`：原地转换整篇文档。识别 `` `path:line` ``、裸 `path:line`、
  `path:line-end` 区间、`[标签](path:line)` 四种写法；围栏代码块跳过；幂等。
  引用必须真实存在（文件存在且行号在范围内）才转换，否则原文保留并计入
  misses 报告（无目录分隔符的弱候选静默跳过，避免 `12:30` 这类误报）。
- `link`：生成单条链接。
- `--root` 缺省从当前目录向上找 `.git`（兼容 worktree）。文档在仓库外
  （如 Obsidian vault）时显式指定。
- 源文件解码：UTF-8 失败回退 CP936（GB2312 代码仓库友好）。

## AI 集成（Claude Code 插件）

本仓库同时是 Claude Code 插件：`skills/cjt` 让 AI 写完文档跑一次
convert，AI 无需手工拼 URL、零额外 token。安装：

```
/plugin marketplace add patrick1099/cjt
/plugin install cjt@cjt
```

## 生效前提

- 已安装 VS Code 扩展 Code Jump Tags（`patrick1099.code-jump-tags`）。
- 点击链接时，VS Code 打开的**第一个**工作区文件夹必须是链接路径的根
  （扩展按 `workspaceFolders[0]` 解析相对路径）。
- 目标行被彻底改写/删除后，跳转退化为按行号定位（扩展既有行为）。

## 兼容性承诺

生成的 URL 与扩展「复制为链接」的输出**字节级一致**：

- pattern = `^[^\S\n]*` + 行文本 trim 后按 TS 字符集 `[.*+?^${}()|[\]\\]` 转义；
- 查询串按 WHATWG `application/x-www-form-urlencoded` 序列化
  （与 JS `URLSearchParams.toString()` 一致：空格→`+`、保留 `*-._`、`~`→`%7E`）。

扩展侧规则如有变更，本仓库测试中的金样用例（`test_golden`）会先失败。
