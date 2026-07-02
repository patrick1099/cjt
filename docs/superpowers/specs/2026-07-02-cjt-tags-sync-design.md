# cjt tags 同步 —— 跳转点写入 .code-jump-tags 设计文档

日期：2026-07-02
状态：已与用户逐节确认（只出设计+计划，暂不实现）
前置：`2026-07-02-cjt-design.md`（cjt 0.1.0，已上线）

## 1. 背景与目标

cjt 0.1.0 只生成无状态链接，刻意不碰 tag store。本特性反转该非目标（opt-in）：
convert/link 翻译出的跳转点可**同步写入 `<工作区根>/.code-jump-tags/store.json`**，
使 Code Jump Tags 扩展侧边栏直接显示这些标签，可导航、可整组管理。

跨两个仓库：

- **cjt → 0.2.0**：新增 store 同步能力（`--tags` / `tags` 子命令）。
- **Lodestar（Code Jump Tags 扩展）→ 0.8.0**：store 文件 watcher（外部修改
  实时刷新侧边栏）+ FolderNode 可选 `source` 字段。

## 2. 非目标

- 不改变 cjt 默认行为：不带 `--tags` 时与 0.1.0 完全一致（纯链接，不碰 store）。
- 不做 store 的多进程锁：扩展保存恰好落在 CLI 读-写之间的毫秒级窗口接受
  （见 §7 已知限制），不引入锁文件协议。
- 不做 trash 交互：CLI 移除的 tag 直接删除，不进扩展回收站（回收站是
  UI 撤销语义，机器同步不适用）。
- 不隐藏 CodeLens/gutter 装饰：写入的 tag 与手工创建的 tag 待遇一致，
  批量同步的文档会在源码里出现对应的 CodeLens 注释行——这是扩展固有
  行为，不在本特性内定制。

## 3. 扩展侧改动（Lodestar 0.8.0）

### 3.1 store 文件 watcher

- `createFileSystemWatcher` 监听 `<workspaceFolders[0]>/.code-jump-tags/store.json`
  （create + change；delete 视为清空可忽略或重载为空 store）。
- **回环防护**：`saveStore()` 前置 guard 标志、写完清除；watcher 回调若
  guard 在置位期间触发则忽略。兜底：回调内读文件与内存 `serialize(cache)`
  比对，一致则跳过（防 guard 时序漏网）。
- 外部变更处理：`loadStore()` + `rebuildTours()`（扩展每次改动即时落盘，
  cache≈disk，直接重载无内存态丢失）。
- 附带收益：手工编辑 store.json、`git pull` 更新 store 也自动刷新。

### 3.2 FolderNode.source 字段

```ts
export interface FolderNode {
  ...
  source?: string;   // 机器同步来源标识(如 cjt 文档相对路径); UI 不展示不编辑
}
```

- `parse()` 已容忍未知字段（直接 cast），`JSON.stringify` 原样保留——
  旧版本扩展读到带 source 的 store 不会损坏它，向后兼容。
- 扩展 UI 对 source 零处理（不显示、改名/拖动均不清除）。唯一语义：
  外部工具用它做 folder 身份匹配。

### 3.3 发版

vsix 打包 + 按惯例同时交付 .vsix 直接安装（自改插件发版惯例）。

## 4. CLI 同步模型（cjt 0.2.0）

### 4.1 数据流（C 形态为核心）

同步的输入是**已转换文档里的 `vscode://` 链接**（不是转换过程的中间态）：
解析 markdown 链接 `[label](vscode://<EXT_ID>/goto?file&line&pattern)`，
还原出 (label, file, line, pattern)。`convert --tags` = convert 后对产物
跑同一提取器；`tags <doc>` = 对现有文档单独跑。两入口一套核心。

- URL 解码：query 按 WHATWG form 规则反解（`+`→空格，`%XX`→字节→UTF-8）。
- 围栏代码块内的链接不提取（与 convert 的跳过规则一致）。
- 非本扩展 authority 的 `vscode://` 链接忽略。

### 4.2 folder 身份与同步语义

- 每文档一个根级 folder：**身份 = `folder.source`**（文档相对工作区根的
  POSIX 路径），**标题 = `--name` 参数**（AI 填友好名），缺省标题 = source。
- 重跑全量同步（对该 folder 内的直接子 tag）：
  - 按 `(file, line)` 匹配：文档里仍存在的 tag **保留原 id 与 createdAt**，
    更新 note（=label）/pattern/text/original；
  - 新出现的引用 → 新建 tag；
  - 文档里已消失的 → 从 folder 移除（直接删除，不进 trash）；
  - folder 内 tag 顺序 = 链接在文档中的出现顺序。
- `--name` 变更只改 title 不换身份（按 source 匹配到既有 folder 后改名）。
- 同一文档内同一 (file,line) 出现多次 → 只建一个 tag，note 取首次出现的
  label（与扩展"一行一 tag"的 findTagByLocation 惯例一致）。
- 找不到 source 匹配的 folder → 在根级**末尾**新建（不挤 inbox 的顶部位）。

### 4.3 tag 字段（复刻扩展创建逻辑）

```json
{
  "type": "tag",
  "id": "t_<Date.now()36进制>_<随机4位36进制>",
  "note": "<链接 label>",
  "file": "<URL file 参数>",
  "line": <URL line 参数>,
  "pattern": "<URL pattern 参数, 无则省略>",
  "text": "<pattern 反解出的行文本(patternToText 逻辑), 无 pattern 则省略>",
  "original": "<同 text>",
  "createdAt": "<ISO 时间, Z 结尾, 毫秒精度——对齐 JS toISOString()>"
}
```

- `original` 在创建时写入是扩展自身的创建语义（"机器不写 original"铁律
  针对的是漂移重锚，不是创建）；同步更新既有 tag 时同步刷新
  text/original/pattern——因为 CLI 的输入源自文档链接（人审内容），语义
  等同扩展的 retargetTag 人工重设通道。
- `text` 由 pattern 反解（剥 `^[^\S\n]*` 前缀 + 去转义，即扩展
  `patternToText` 的 Python 复刻）。

### 4.4 link --tags（单条进收件箱）

- 复刻 `getOrCreateInbox`：根级第一个 `inbox:true` folder；无则在根级
  **顶部**创建 `{title:"未分组", inbox:true}`（id `f_` 前缀同款格式）。
- 同 (file,line) 已存在 tag（全树范围）→ 更新其 note 而非新建（对齐扩展
  一行一 tag 惯例）；否则新 tag 追加到 inbox。

## 5. CLI 接口（增量）

```
cjt convert <doc.md> [--tags] [--name 名称] [其余参数同 0.1.0]
cjt tags <doc.md> [--name 名称] [--root DIR] [--format json|text]
cjt link <ref> [LABEL] [--tags] [其余参数同 0.1.0]
```

- `--name` 仅在 `--tags`/`tags` 下有意义；`link --tags` 不接受 `--name`
  （进 inbox）。
- JSON 报告增量：convert/tags 带 `"tags": {"folder": "<title>",
  "added": n, "updated": n, "removed": n}`；link --tags 带
  `"tag": {"folder": "未分组", "action": "added"|"updated"}`。
- `tags` 对无任何本扩展链接的文档：正常退出 0，报告 added/updated/removed
  全 0（不是错误）。

## 6. 序列化与写入安全

- **格式逐字节对齐扩展 `serialize()`**：`json.dumps(store, ensure_ascii=False,
  indent=2)`（JS `JSON.stringify(store, null, 2)` 不转义非 ASCII、逗号冒号
  间距与 Python indent 模式默认一致）、无尾随换行、键序=插入序（构造时
  按扩展字段顺序插入）。保证 git diff 干净、往返无损。
- 原子写：临时文件 + `os.replace`（同 0.2.0 convert 写回模式）。
- store 不存在 → 创建目录 + 空 store `{"version": 1, "tree": []}` 再合并。
- store 损坏（JSON 解析失败或 version≠1）→ 硬错误退出 1，**绝不覆盖**
  原文件（扩展的 parse 遇损坏静默清空，CLI 不复刻该行为——CLI 覆盖等于
  帮用户丢数据）。
- 廉价缓解竞态：读 store 时记 mtime，`os.replace` 前复核 mtime 未变，变了
  则重读重合并一次（一次重试足够，仍变则报错退出）。

## 7. 已知限制（写入 README）

- 扩展保存恰好落在 CLI"重试后的读-写"之间仍可能被覆盖（双重毫秒级窗口，
  个人工具接受）。
- 扩展未升级到 0.8.0 时：CLI 写入照常工作，但 VS Code 开着时侧边栏不刷新
  （需 Reload Window），且此后扩展内的任何 tag 操作会以旧 cache 全量写回、
  **抹掉 CLI 写入**——README 明确要求配套 0.8.0。
- 多根工作区只认第一个文件夹（沿用扩展 `workspaceFolders[0]` 语义）。

## 8. 测试

### cjt 侧

- Core 纯函数（dict-in-dict-out 直测）：
  - 链接提取：label/file/line/pattern 反解、WHATWG 反解码（`+`/`%XX`/中文）、
    围栏内忽略、非本扩展链接忽略；
  - patternToText 反解：前缀剥离、去转义、与 `line_pattern()` 互逆金样；
  - folder 同步 merge：首建（末尾）、增/改/删、id+createdAt 保持、
    `--name` 改题不换身份、同行去重、顺序=文档序；
  - inbox：复用第一个 inbox / 顶部惰性创建 / 全树 (file,line) 去重更新；
  - 序列化金样：与扩展 `JSON.stringify(store, null, 2)` 产物逐字节一致
    （含中文 note、可选字段省略）。
- App e2e（临时目录真文件）：`tags` 首跑/重跑幂等、`convert --tags` 一步
  到位、损坏 store 拒写退出 1 且原文件未动、mtime 竞态重试路径。

### 扩展侧（0.8.0）

- 单测（vitest，纯逻辑）：guard 置位期间忽略回调、内容比对兜底跳过、
  外部变更触发 reload 路径。
- 真机手工：CLI 写入 → VS Code 开着侧边栏实时出现 folder；扩展内删 tag
  → CLI 重跑 → 互不踩（CLI 会按文档重新补回——预期行为，文档说明）。

### 人工 gate

真机跑 `convert --tags`，侧边栏实时出现；点击侧边栏 tag 跳转正确；
`--name` 重跑改标题；扩展 0.7.x（未升级）下验证"写入生效但需 reload"。
