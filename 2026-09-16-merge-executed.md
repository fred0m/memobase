# memobase 插件合并：v4 × 公开治理（执行记录）

**日期**: 2026-09-16
**状态**: ✅ 已合并并落地生产，回归全绿
**合并提交**: `c245b4e`（生产 `feature/summary-layer-v4` 主线）

---

## 一、前置确认：公开 PR 与本地完全无关

实测四项证据：

| 检查 | 结果 |
|---|---|
| 生产仓 `git remote -v` | **空** —— 与公开仓库无任何连接 |
| 生产代码引用 `fred0m`/GitHub | **0 处** |
| catalog sidecar（`.hermes-catalog.json`） | **不存在** —— 手工放置，非 catalog 安装 |
| PR 改的文件在本机 | `~/.hermes/hermes-agent/plugin-catalog/memobase.yaml` **不存在** |

→ PR #112551 合不合都不影响本地。合并动因是「避免维护两份」，
不是「等 PR 结果」。

---

## 二、合并执行

### 拓扑与冲突

共同祖先 `290928d`（v3 尖端）；本地 5 个 v4 提交 vs 公开 4 个提交，互不重叠。

```
冲突合计: 2 块 / 23 行
  __init__.py        1 块（17 行）— docstring：v4 的 anchor_* 说明 vs 公开的 cjk/rerank 说明
  hybrid_retriever.py 1 块（6 行）— import：Any/Callable/Dict… vs 新增 Path
```

**零语义冲突**，两边都保留即可。已提交于隔离克隆 `/tmp/mb-merge-sim`，
通过 `git fetch + merge --ff-only` 落入生产仓，**不做手工解冲突**。

### 冲突之外暴露的一个真问题

合并后公开的测试挂 1 个：

```
FAIL: test_prefetch_returns_context
AssertionError: '小爱正在回忆' != 'memobase'
```

两边意图不同（家里要人设文字、公开要通用文字）→ 改为**配置驱动**：

```python
self._recall_label = str(cfg.get("recall_label") or "").strip() or "memobase"
self._recall_glyph = str(cfg.get("recall_glyph") or "").strip() or "🧠"
```

公开默认 `memobase` / 🧠，家里配 `小爱正在回忆` / 💭。两边都满足。

---

## 三、配置回填（仅 2 项）

合并后代码可读 37 个配置键；家里原有 9 个。

### 必须回填（已写入 `~/.hermes/memobase.json`）

| 键 | 值 | 不回填的后果 |
|---|---|---|
| `recall_label` | `小爱正在回忆` | 提示变通用 "memobase"，人设文字丢失 |
| `recall_glyph` | `💭` | 图标变 🧠 |

### 无需回填

其余 27 个键的**代码默认值恰好等于生产现值**（实测：纯 v4 vs 合并版
25 个共同配置项全部一致）。关键项 `event_budget_tokens`=900 家里已配，
是合并后的优先键，`hybrid_event_budget_tokens` 仅作 fallback。

### 已有强依赖（不能丢，已确认在位）

| 键 | 值 | 后果 |
|---|---|---|
| `rerank_base_url` | `https://api.siliconflow.cn/v1` | **丢了 rerank 静默关闭**（公开版删了默认端点） |
| `cjk_known_names` | 9 个名字 | 丢了实体腿认不出家里人名 |

---

## 四、回归测试（落地生产后执行）

| # | 项目 | 结果 |
|---|---|---|
| 1 | 语法编译 `py_compile` | ✓ |
| 2 | 单元测试（生产目录） | **39/39 OK** |
| 3 | `hermes plugins validate` | passed ✓ |
| 4 | `hermes memory status` | installed ✓ / available ✓ |
| 5 | **端到端召回（真实服务）** | **6/6 查询输出逐字节一致** |
| 6 | gateway 重启后日志 | `hybrid recall OK: 15 summary lines, 16 event lines` ✓ |
| 7 | 桌面端重启后日志 | 见下「多进程对齐」✓ |

### 多进程对齐（第 4 层回归之外的补充）

插件是**进程启动时一次性导入**的，磁盘改了 ≠ 在跑的进程改了。
本机有两层进程持有该插件：

| 进程 | 启动时间 | 代码 | 处置 |
|---|---|---|---|
| gateway (4417) | 09-16 13:41 | 新 ✓ | 合并后立即重启 |
| 桌面端 serve (61154→25385) | 09-15 14:41 → 重启后 15:37 | 旧 → 新 ✓ | 由沫沫重启 |

**未重启时先论证「行为等价」再决定要不要打断用户**：三处差异逐一比对现行配置值，
全部相等（硬编码 9 名字 == 配置 9 名字；默认端点 == 配置端点；人设文字 == 配置文字），
因此旧进程与新代码在本生产配置下行为一致 —— 不重启也不会断层。
但建议重启使进程内存与磁盘对齐，避免以后排查时混淆。

**重启后验证「进程真换了代码」三证据**：

```
① ps -p 25385 -o lstart  → Wed Sep 16 15:37:47（晚于代码改动 13:40:32）✓
② __pycache__/__init__.cpython-311.pyc → Sep 16 13:40:42（源码改后 10 秒编译）✓
   （同目录 312/314 版本的 .pyc 仍是 8/30、9/1 —— 只有当前进程用的 311 更新了）
③ 15:37:56 日志：Memory provider 'memobase' registered (2 tools) / activated /
   initialized ... writes=True；15:38:02 首条 hybrid recall OK ✓
```

重启后四项配置实测生效：`recall_label`=小爱正在回忆、`cjk_known_names`=9 个、
`rerank_base_url` 可用=True、`summary_enabled`/`anchor_backfill`=True。

### 端到端回归方法（关键）

不是单元测试——是真的走 HTTP + 混合检索 + rerank 门控 + 概要段：

```
合并前（纯 v4）vs 合并后，各跑同一批 6 条查询，比对 prefetch 输出 SHA256：
  6/6 完全一致
```

配置生效实测：

```
recall_label       = 小爱正在回忆
recall_glyph       = 💭
summary_enabled    = True
anchor_backfill    = True
rerank 可用         = True
event_budget       = 900
hybrid_keep        = 10
实体腿名单          = 9 个（图图/沫沫/小爱/…）
实体抽取实测        = ['图图', 'memobase']
recall_status      = ('小爱正在回忆', '💭')
```

---

## 五、备份与回退

| 备份 | 路径 |
|---|---|
| 插件目录全量 | `~/.hermes/backups/memobase-plugin-before-merge-20260916-134023/` |
| 配置 | `~/.hermes/backups/memobase.json.before-merge-20260916-134023` |
| 更早（v3 公开前） | `~/.hermes/backups/memobase-plugin-before-v3pub-20260916-103842/` |
| 配置（cjk） | `~/.hermes/backups/memobase.json.before-cjk-20260916-104418` |
| 配置（rerank） | `~/.hermes/backups/memobase.json.before-rerank-20260916-105459` |

**回退动作**：

```bash
cd ~/.hermes/plugins/memobase
git reset --hard 44bd75a          # 回合并前
cp ~/.hermes/backups/memobase.json.before-merge-20260916-134023 ~/.hermes/memobase.json
hermes gateway restart
```

---

## 六、后续维护约定

- **公开仓库 = 本地产线的上游**。以后改公开版，本地直接 `git merge pub/main`，
  不再手工同步两份。
- **v4 概要层保持私有**（未通过 SC-001 验收，不外推）。
- 若将来合并「脱敏后的 v4」到公开，需注意 `recall_label` / `recall_glyph` /
  `cjk_known_names` / `rerank_base_url` 四项配置必须随 `memobase.json` 一起迁移
  —— 建议进备份与迁移清单。
