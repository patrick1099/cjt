---
name: cjt
description: Code Jump Tags 两件事的统一入口。(1) 侧边栏分层标签——用户说"用 cjt 做 X 链路追踪 / 打标签 / 在侧边栏建文件夹 / 按分类列跳转点 / 顺读教程"时，用扩展自带的 cjtag CLI 把代码位置按任意层级文件夹写进 VS Code 侧边栏，不写 md 文档。(2) 文档跳转链接——写完引用了代码位置的教程/报告/文档，或用户要 jump links / 跳转链接时，把 markdown 里的 path:line 批量转成 vscode:// 深链（VS Code 与 Obsidian 可点）。Do NOT hand-craft vscode:// URLs or edit store.json by hand.
---

# cjt — Code Jump Tags

先分清用户要的是哪一样，**两条路不混用**：

| 用户要的 | 走哪条 | 产物 |
|---|---|---|
| 「用 cjt 做 X 链路追踪」「打标签」「侧边栏建文件夹 / 分类」「顺读」 | **A. cjtag 分层标签** | 侧边栏文件夹树，不产出 md |
| 写好的文档里的代码引用要能点 | **B. cjt.py 文档链接** | 原 md 里的引用变成 vscode:// 链接 |

「链路追踪」的默认理解是 **A**：用户要的是侧边栏里按环节分类、能顺读的跳转点。
别为了 A 先写一份 md 再用 B 的 `--tags` 转——那只会出一层平铺文件夹，没有分类。

## A. 侧边栏分层标签（cjtag）

Code Jump Tags 扩展 ≥ 0.9.0 自带 CLI `cjtag`（在扩展目录的 `dist/cli.js`）。用 Bash 工具定位最新版：

```
CLI=$(ls -d ~/.vscode/extensions/patrick1099.code-jump-tags-* | sort -V | tail -1)/dist/cli.js
node "$CLI" --ai-help        # 完整说明
```

### 流程

1. **读代码，列节点**：沿链路把每个关键位置记成 `file + line + 一句话讲解`。
   行号用 Read/Grep 实读确认，别凭印象。
2. **分层**：`folder` 用 `/` 分层，任意深度，路径上不存在的层逐级自动建。
   - 一级 = 这条链路的名字（如 `预留量链路`）；
   - 二级 = 链路环节，带序号定顺序（如 `1 出厂默认`、`2 设置`、`4 扣减与用尽`）；
   - 三级按需：某环节里节点多或有明显子类时再拆（如 `2 设置/平台 0008`、`2 设置/公共转换`）。
     节点少的环节不硬拆。
   - 数组顺序 = 阅读顺序（顺读模式按它走），同一文件夹的条目按调用先后排。
3. **写清单**：UTF-8 JSON 扁平数组放在 scratchpad（不放进被标注的仓库），每条：
   ```json
   {"folder": "预留量链路/2 设置/平台 0008", "file": "Code/App/Code/xxx.c", "line": 1094, "note": "0008 读写入口"}
   ```
   `file` 相对工作区根；`note` 写**这一行在链路里干什么**，短句，可带发现的疑点（如「只清 Event，不清 WaitAuth」）。
4. **导入并核对**：
   ```
   node "$CLI" import <tags.json> --cwd <仓库根>
   node "$CLI" list --folder "<一级名>" --cwd <仓库根>
   ```
   某条校验不过（文件不存在 / 行号越界 / note 空）整批拒绝、不写入，按 `error.details` 的 index 修。

### 追加、重建、删除

- **往某个子文件夹追加**（含新建更深一层）：只导**新增**条目，或单条
  `node "$CLI" add --folder "预留量链路/4 扣减与用尽/用尽处理/新夹" --file F --line N --note TEXT --cwd <仓库根>`。
- **import 不去重**：同一份清单导两次会出重复标签（实测）。要整棵重建，先
  `node "$CLI" clear --folder "<一级名>" --cwd <仓库根>` 再 import。
- `clear` 进回收站，用户可在侧边栏「从回收站恢复」；清之前先 `list` 看一眼确认是要清的那棵。
- store.json 有文件监听，写完侧边栏即时刷新，不用重载窗口。

### 交付时告诉用户

- 树的形状（文件夹层级 + 各夹条数），不用逐条复述 note；
- 每层文件夹上的 ▶「顺读此文件夹」可以从头读完整条链路；
- 追踪中发现的疑点单独列出，说明没改代码。

## B. 文档跳转链接（cjt.py）

写文档时**正常写引用**，不要手工拼 URL。支持的引用写法（转换时自动识别）：

- `` `App/Code/main.c:123` ``（行内代码，最常用）
- `App/Code/main.c:123` 裸文本 / `App/Code/main.c:123-145` 行区间
- `[自定义标签](App/Code/main.c:123)`
- 裸引用与中文文字之间要留空格（或用行内代码包起来），否则路径会被误粘连

围栏代码块内的引用不会被转换；已是 `vscode://` 的链接不会二次转换（幂等）。

文档写完后执行一次：

```
py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/cjt.py convert <doc.md> --format json
```

- 报告中 `misses` 为空 → 完成。
- 有 miss → 按 `reason` 核对该引用（`file-not-found`/`line-out-of-range` 通常是路径或行号写错；路径必须相对**工作区根**），改正文档后重跑。
- 文档在被引用仓库之外（如 Obsidian vault）→ 加 `--root <仓库根>`。
- 只要一条链接：`py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/cjt.py link path:line [标签] --format json`。
- 文档本身就是主体、只想在侧边栏留一个「这份文档的跳转点」平铺文件夹时，convert 加
  `--tags --name "<名称>"`；重跑幂等，文档删改后再跑一次即同步。**要分类/分层就走 A。**
  已转换文档单独补写：`py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/cjt.py tags <doc> --name <名称> --format json`。
- 单条进收件箱：`link path:line [标签] --tags`。

## 前提（对人说明，遇到"点了没反应"时提示用户）

- 需已安装 VS Code 扩展 Code Jump Tags（patrick1099.code-jump-tags）；A 需 ≥ 0.9.0。
- 点击时 VS Code 打开的第一个工作区文件夹必须是链接路径的根。
- 侧边栏实时刷新需扩展 ≥ 0.8.0；旧版需 Reload Window。
