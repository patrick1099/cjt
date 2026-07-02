# cjt — Code Jump Tags CLI 设计文档

日期：2026-07-02
状态：已与用户逐节确认

## 1. 背景与目标

Code Jump Tags（VS Code 扩展，发布名 `patrick1099.code-jump-tags`，源码在
`plugin-research/codetour`）支持无状态深链：

```
vscode://patrick1099.code-jump-tags/goto?file=<相对路径>&line=<1起行号>&pattern=<行内容正则>
```

点击链接即在 VS Code 中打开文件、跳到行，`pattern` 提供代码漂移恢复。
链接**不要求 store 里存在对应 tag**——file/line/pattern 全部编码在 URL 里。

AI（Claude Code 等）写教程、排查报告、学习笔记时天然会写
`App/Code/main.c:123` 这类代码引用。本工具把这些纯文本引用批量转换成
上述可点击跳转链接，使文档在 VS Code、Obsidian（`vscode://` 属外部链接，
点击唤起 VS Code）中都可直接跳转到代码。

**核心原则：无感、零额外 token。** AI 按本来的习惯写引用，写完跑一次
CLI 即可，不需要 AI 逐链接思考、拼 URL、转义正则。

## 2. 非目标

- **不做 hook 全自动改写**（PostToolUse 监听 Write/Edit）：AI 不知情的
  文件改写有风险，且并非所有 `path:line` 都想变链接。
- **不做符号引用解析**（如 `User_TaskManage()` 不带行号让 CLI 自己 grep
  定义处）：多处定义有歧义，AI 引用几乎总带行号，YAGNI。
- **不创建/修改 tag store**（`.code-jump-tags/`）：只生成无状态链接，
  不污染用户的标签树。
- **不解决扩展侧的工作区约束**：`gotoLocation` 用 `workspaceFolders[0]`
  解析相对路径，链接只在 VS Code 以对应仓库为第一工作区文件夹打开时
  有效。此为扩展既有行为，CLI 仅在 README 中说明。

## 3. 总体形态与仓库结构

- Python 3 **纯 stdlib** 单脚本，`py -3` 直接运行，不上 venv。
- 独立**公开**仓库 `patrick1099/cjt`，本地 `~/.claude/plugins-dev/cjt`，
  注册进 xu-local marketplace。结构仿 keil2clangd：

```
cjt/
├── .claude-plugin/
│   ├── plugin.json          # Claude Code 插件清单
│   └── marketplace.json
├── scripts/
│   ├── cjt.py               # CLI 本体（单文件）
│   └── test_cjt.py          # stdlib unittest
├── skills/
│   └── cjt/
│       └── SKILL.md         # AI 触发入口
├── docs/superpowers/specs/  # 本文档
├── README.md                # 用法 + 工作区约束说明 + 与扩展的兼容性承诺
├── LICENSE                  # MIT
└── .gitignore
```

## 4. CLI 接口

```
py -3 cjt.py convert <doc.md> [--root DIR] [--dry-run] [--encoding ENC] [--format json|text]
py -3 cjt.py link <path:line[-end]> [LABEL] [--root DIR] [--encoding ENC] [--format json|text]
```

### convert（主入口）

- 原地改写文档，把识别到的代码引用替换为跳转链接。
- `--dry-run`：只输出报告，不写文件。
- 输出报告（`--format json`，AI 专用；`text` 给人看）：

```json
{
  "doc": "教程.md",
  "root": "C:/Users/<user>/Desktop/.../wt-alpha",
  "converted": 5,
  "misses": [
    {"ref": "App/Code/foo.c:999", "line_in_doc": 42, "reason": "line-out-of-range"},
    {"ref": "App/Code/gone.c:10", "line_in_doc": 57, "reason": "file-not-found"}
  ]
}
```

### link（附赠子命令）

- 生成单条链接，供 ad-hoc 使用。
- `LABEL` 缺省时标签为 `` `path:line` ``（行内代码样式）。
- `--format json` 输出 `{"markdown": "...", "url": "..."}`。

### --root 解析

- 缺省：从 cwd 向上找 `.git`（文件或目录均可，兼容 worktree）。
- 显式传入仅用于两种边角情况：文档在被引用仓库之外（如 Obsidian
  vault），或 VS Code 打开的工作区文件夹不是 git 根。
- 找不到根且未显式指定 → 硬错误退出。

## 5. 引用识别规则（convert）

识别四种写法（`path` 可含 `/` 或 `\`，可为绝对路径）：

| # | 写法 | 示例 | 转换后标签 |
|---|------|------|-----------|
| 1 | 行内代码 | `` `App/Code/main.c:123` `` | `` [`App/Code/main.c:123`](url) ``（代码样式保留） |
| 2 | 裸文本 | `App/Code/main.c:123` | `[App/Code/main.c:123](url)` |
| 3 | markdown 链接 | `[主循环](App/Code/main.c:123)` | `[主循环](url)`（自定义标签保留） |
| 4 | 行区间 | `App/Code/main.c:123-145` | 同 1/2/3，跳转点取起始行 123 |

跳过规则：

- **围栏代码块（``` / ~~~）内一律跳过**；行内代码（写法 1）要转——
  那是 AI 写引用最常用的形式。
- 目标已是 `vscode://` 的链接跳过 → **幂等**，重复 convert 无副作用。
- **最终安全闸门：文件必须真实存在且行号 ≤ 文件行数**。候选引用先按
  root 解析路径，文件不存在或行号越界 → 原文保留 + 进 misses。
  `12:30`、`http://x:80` 之类的误匹配因此天然不可能被转换。
- 绝对路径引用自动相对化到 root；root 之外的文件 → 原文保留 + miss
  （reason: `outside-root`）。

行号越界按 miss 处理（不降级为无 pattern 的链接）：越界通常意味着引用
已过期，静默生成一个跳不准的链接比报告出来更糟。

## 6. 链接生成 —— 与扩展严格同源

复刻扩展 `src/lodestar/commands.ts` 的 `tagLinkMarkdown()` 与
`src/lodestar/relocate.ts` 的 `linePattern()`：

- URL 形态：`vscode://patrick1099.code-jump-tags/goto?file=..&line=..&pattern=..`。
  扩展 ID 作为脚本内常量（authority 必须是 publisher.name，与扩展
  `EXTENSION_ID` 保持一致）。
- `file`：root 相对路径，统一 `/` 分隔符。
- `pattern` = `^[^\S\n]*` + 目标行 trim 后逐字转义。**转义字符集必须与
  TS 完全一致**：`[.*+?^${}()|[\]\\]`（用 `re.sub` 实现，**不用**
  Python `re.escape`——其转义集不同，会破坏字节级一致）。
- 目标行为空白行 → 不带 `pattern` 参数（与扩展 `linePattern()` 返回
  undefined 的行为一致）。
- URL 编码用 `urllib.parse.urlencode`（空格→`+`，与 JS
  `URLSearchParams.toString()` 一致）。
- **源文件编码**：先按 UTF-8 严格解码，失败回退 CP936（固件仓库为
  GB2312），`--encoding` 可显式覆盖。pattern 中的中文注释必须正确解码
  为 Unicode 才能与 VS Code 侧匹配。文档本身固定按 UTF-8 读写。

## 7. skill 集成（"无感"的落点）

`skills/cjt/SKILL.md`：

- **触发**：写含代码引用的教程/文档/报告，或用户提到"跳转链接 /
  code jump tags"。
- **指令**（一句话级别）：正常写文档，代码引用照常写 `path:line`；
  写完执行
  `py -3 ${CLAUDE_PLUGIN_ROOT}/scripts/cjt.py convert <doc> --format json`，
  检查 misses 为空即完成；有 miss 则核对该引用的路径/行号后重跑。

learning-skill-plus 等现有 skill 不改动——本 skill 独立触发即可覆盖
"AI 做教程给人看"的场景。

## 8. 错误处理

- 单条引用失败不影响整篇：原文保留 + 记入 misses。
- misses 非空时退出码仍为 0（报告即成功）；文档不存在、root 无法解析、
  文档非 UTF-8 等硬错误 → 非零退出 + stderr 说明。
- 改写采用"全文处理完再一次性写回"，中途异常不落半成品文件。

## 9. 测试策略

`scripts/test_cjt.py`，stdlib unittest，纯函数直测：

1. **引用识别**：四种写法、围栏代码块跳过、行内代码转换、幂等
   （已转链接再跑不变）、误匹配（`12:30`、URL 端口）不转换。
2. **pattern 转义**：TS 字符集逐字符对拍；空白行无 pattern；中文行。
3. **整文档转换**：混合样例 markdown 的前后对照（golden）；misses
   报告结构。
4. **兼容性金样**：用扩展"复制为链接"真实产出的 URL 做字节级断言，
   锁定两侧一致（扩展 pattern 规则若变，此测试先红）。
5. **root/路径**：绝对路径相对化、`\`→`/`、root 外 miss、worktree
   的 `.git` 文件探测。

## 10. 已知约束（写进 README）

- 链接生效要求 VS Code 已安装 Code Jump Tags 扩展，且打开的**第一个**
  工作区文件夹是链接路径的根。
- Obsidian 中 `vscode://` 作为外部链接可点击；阅读视图/实时预览均可。
- pattern 提供漂移恢复，但目标行被彻底改写/删除后跳转会退化为按行号
  定位（扩展既有行为）。
