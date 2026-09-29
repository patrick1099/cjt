---
name: cjt
description: Code Jump Tags 入口。(1) 用户说"用 cjt 做 X 链路追踪 / 打标签 / 带我走一遍代码"时，按场景写引导式检查点（告诉用户用 F12、调用层次、查找引用、全局搜索怎么手动跳），用扩展自带 cjtag CLI 写进 VS Code 侧边栏，不写 md。(2) 文档里的 path:line 引用要能点时，用 cjt.py 批量转成 vscode:// 深链。不要手拼 vscode:// URL，不要手改 store.json。
---

# cjt — Code Jump Tags

| 用户要的 | 走哪条 |
|---|---|
| 链路追踪、打标签、带我走一遍 | **A. cjtag 引导式检查点** |
| 文档里的代码引用要能点 | **B. cjt.py 文档链接** |

## A. 引导式链路追踪（cjtag）

标签是**检查点**，引导用户自己跳，不是让他按 Alt+] 顺读。每条 note =
「这一行看什么」+「▶ 下一步：用什么跳转、在哪个符号上、到哪」。

**note 要短**：一句看点 + 一个操作，如 `3.4 函数指针，F12 死路。▶ 1630 行表名 F12`。
不写「怎么找到的」长解释、不复述代码、不写背景；解释留给交付时的回复。

用户否掉过：按类别摆（不连贯）；只排执行顺序让他顺读（学不会自己追）；note 写成段落（废话多）。

```
CLI=$(ls -d ~/.vscode/extensions/patrick1099.code-jump-tags-* | sort -V | tail -1)/dist/cli.js
```

### 流程

1. **锁定 target**：多 target 的仓库先确认是哪个，只走它编进去的 `#if` 分支，链路名标明（如 `预留量链路（商用超声波）`）。
   看 `.clangd` 配的是哪个 target，引导语要和编辑器里看到的一致。
2. **场景 0 起点**：核心字段、相关枚举上 Shift+F12，setter/getter 上看调用层次，列出全部读写点并标注属于哪个场景。
3. **按场景写检查点**：场景 =「触发 → 结果」（上电、各写入口、消耗到阈值、解除；疑点路径单独成场景）。
   每个场景第一步写「从场景 0 哪一处、用什么跳转找到的」，然后从真实入口往下走。
   检查点放在用户要操作的那一行；同函数内的写「往下看 N 行」；异步（定时器、回调）要点明。
4. **编号**：`场景号.步号`，不补零（`3.4`、`3.12`），互相引用也用它。
5. **跳转必须实测**：写每条 ▶ 之前用 LSP（goToDefinition / incomingCalls / findReferences）验证能到。
6. **核对并导入**：清单是 JSON 数组（顺序即追踪顺序），放 scratchpad：
   ```json
   {"folder": "链路名/3 扣到用尽 → 关阀", "file": "相对仓库根", "line": 1648, "note": "3.4 查表后函数指针调用，在 ExeDataID 上 F12 是死路。▶ 在 1630 行表名上 F12"}
   ```
   ```
   py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/check_steps.py <json> --root <仓库根>   # 逐条打印源码行，0 错误再导
   node "$CLI" import <json> --cwd <仓库根>
   ```

### 跳转规律（clangd 实测，换仓库复测）

| 追什么 | 用 | 坑 |
|---|---|---|
| 往里走 | F12 | |
| 谁调了我 | 显示调用层次结构 | 函数指针传参也能找到；会混入其他 target 的调用者 |
| 查表分发的函数往上 | 调用层次 → 停在表变量 → 表名上 Shift+F12 | |
| 变量/字段/枚举读写 | Shift+F12 | 最准 |
| 宏 | Ctrl+Shift+F | Shift+F12 只有定义；F12 只给生效的那个定义 |
| `table[i].fn()` | 表名上 F12 → 找项 → 函数名上 F12 | 成员名上 F12 是死路 |
| 返回 | Alt+← 或点前面的检查点 | |

有透明加密的机器，先用 VS Code 自带 `rg.exe` 实测全局搜索能否搜到源文件明文。

### 维护

- `import` 不去重。整棵重建：先 `clear --folder "<链路名>"` 再 import（进回收站可恢复）。
- 追加：只导新条目，或 `add --folder --file --line --note`；插到场景中间就整个场景重导。
- 标签多了嫌乱：`hide --folder "<链路名>"` 隐藏编辑器标记（子文件夹跟着隐藏，侧边栏照常列、点哪条临时显示哪条），`show` 恢复。需扩展 ≥ 0.9.4。

### 交付

场景列表 + 各多少检查点；建议从场景 0 开始；疑点单列（场景.步号），说明没改代码。

## B. 文档跳转链接（cjt.py）

正常写 `` `path:line` ``、`path:line-end`、`[标签](path:line)`，写完跑：

```
py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/cjt.py convert <doc.md> --format json
```

- `misses` 为空即完成；有 miss 按 `reason` 改路径/行号（相对工作区根）后重跑。
- 文档在仓库外加 `--root <仓库根>`；单条链接用 `link path:line [标签]`。
- `--tags --name` 只出一层平铺文件夹，要分层走 A。

## 前提

需装 VS Code 扩展 patrick1099.code-jump-tags（A 需 ≥ 0.9.0）；VS Code 打开的第一个文件夹要是链接路径的根。
