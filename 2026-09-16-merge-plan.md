# memobase 插件：本地 v4 与公开 v3 合并方案

**日期**: 2026-09-16
**背景**: 公开仓库 `fred0m/hermes-memobase-provider` 与本地 `~/.hermes/plugins/memobase`
已成两条分叉线，存在「维护两份」的风险。

---

## 一、当前拓扑（实测）

```
                    common ancestor
                    290928d (v3 尖端, 8/29)
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
  本地 feature/summary-layer-v4      公开 main (pub/main)
  5 个 commit（概要层）                4 个 commit（脱敏 + rerank 治理）
  ├ dbef859 summary layer             ├ 3ae6336 v1（远古，独立历史）
  ├ 99e9fa1 BM25 line ranking         ├ c639bcd hybrid v3 + 脱敏
  ├ 655dd1e keep summary lines        ├ 75d3b0e 测试规范
  ├ ebe59fe FR-019 anchor backfill    └ 4e8e447 rerank 无默认端点
  └ 44bd75a anchor budget fix
```

**关键**：共同祖先是 v3 尖端，**v4 的 5 个提交与公开的 4 个提交互不重叠**，
所以是「各加各的」而非「互相改写」。

---

## 二、合并实操结果（已模拟验证，未落生产）

试合并 `pub/main` → `feature/summary-layer-v4`：

| 文件 | 结果 |
|---|---|
| `README.md` | 自动合并 ✓ |
| `plugin.yaml` | 自动合并 ✓ |
| `LICENSE` / `tests/` | 新增 ✓ |
| `__init__.py` | **1 个冲突块（17 行）** |
| `hybrid_retriever.py` | **1 个冲突块（6 行）** |

冲突总量：**2 块 / 23 行**，且全是「两边各加一段」的机械冲突：

1. `__init__.py` — 文档字符串里，v4 加了 anchor_* 说明，公开加了 cjk/rerank 说明
   → 两边都保留即可
2. `hybrid_retriever.py` — import 行，v4 用 `Any, Callable, Dict...`，公开加了 `Path`
   → 取并集

**无任何语义冲突。**

---

## 三、合并后暴露的一个真问题（已解）

合并后跑测试：**35 个里挂 1 个**

```
FAIL: test_prefetch_returns_context
AssertionError: '小爱正在回忆' != 'memobase'
```

**这不是谁对谁错，是两边意图不同**：

- 本地 v4：`recall_status` 显示「小爱正在回忆 💭」—— 人设文字
- 公开版：显示 `memobase` / 🧠 —— 通用文字

**解法**：把它做成配置项，两边都满足。

```python
# 公开默认
self._recall_label = str(cfg.get("recall_label") or "").strip() or "memobase"
self._recall_glyph = str(cfg.get("recall_glyph") or "").strip() or "🧠"
```

实测：

```
家里配置 -> label= 小爱正在回忆  glyph= 💭
公开默认 -> label= memobase     glyph= 🧠
测试：35/35 OK
```

至此**公开包零人设文字**，家里行为不变。

---

## 四、配置项回填分析

合并后代码可读 **37** 个配置键，家里当前配了 **9** 个。

### ✅ 无需回填（有默认值，且默认值 == 生产现值）

绝大多数属于此类。决定性验证：用家里配置分别实例化「纯 v4」和「合并版」，
对比 25 个共同配置项：

```
✅ 全部一致 —— 合并未改变任何生效配置
```

具体关键项：

| 键 | 生效值 | 说明 |
|---|---|---|
| `event_budget_tokens` | 900 | **家里已配**，是合并后的优先键 ✓ |
| `hybrid_event_budget_tokens` | — | 仅作 fallback，被上者覆盖 |
| `temporal_gain` / `floor` / `half_life` | 1.6 / 0.6 / 30 | 代码默认 == 生产现值 |
| `hybrid_keep` | 10 | 同上 |
| `timezone` | Asia/Shanghai | 同上 |
| `rerank_model` / `rerank_topk` / `rerank_keep` | Qwen3-Reranker-4B / 15 / 8 | 同上 |
| `anchor_*` (4 项) | true / 2 / 2 / 200 | v4 默认就是生产现值 |

### ⚠️ **必须回填**（仅 2 个）

| 键 | 值 | 不回填的后果 |
|---|---|---|
| `recall_label` | `小爱正在回忆` | 记忆提示变成通用的 "memobase"，人设文字丢了 |
| `recall_glyph` | `💭` | 图标从 💭 变 🧠 |

**这就是你问的「配置项回填」—— 答案：只有 2 个，而且都是显示层，不影响召回质量。**

### ⚠️ 已有的强依赖（不能丢）

| 键 | 值 | 后果 |
|---|---|---|
| `rerank_base_url` | `https://api.siliconflow.cn/v1` | **丢了 rerank 会静默关闭**（公开版删了默认端点） |
| `cjk_known_names` | 9 个名字 | 丢了实体腿认不出家里人名 |

---

## 五、建议方案

### 推荐：合并到本地，让公开仓库成为本地的上游

**不是**「把 v4 也推公开」（那会把概要层这个未验收的东西也给出去），
**而是**：本地把公开的治理成果（脱敏 + rerank 安全）吸收进来，从此
`pub/main` 是可合并的上游，本地只保留 v4 增量。

```
本地 feature/summary-layer-v4
   ← merge pub/main（解决 2 个冲突块）
   ← 回填 2 个配置键
   ← 跑 35 测试 + 配置等价性验证
```

**收益**：
- 脱敏规则（无硬编码人名、无默认端点）只在公开侧维护一次
- 以后再改公开版，本地直接 merge，不用手工同步两份
- 本地 v4 的概要层保持私有，未验收不会外泄

**不做的事**：不推 v4 到公开仓库（概要层还没通过 SC-001 验收）。

### 执行顺序（建议）

1. 先在本地仓库开合并分支（不动 `feature/summary-layer-v4` 主线）
2. 解冲突 → 回填 2 键 → 跑验证
3. 验证通过后合入 v4 主线
4. 生产插件是软链/同目录，合并后行为需再验一次

---

## 六、验证证据（本次模拟全部实测）

| 项 | 命令/方法 | 结果 |
|---|---|---|
| 冲突规模 | `grep -c '^<<<<<<<'` | 2 块 / 23 行 |
| 语法 | `python -m py_compile` | 通过 ✓ |
| 测试 | `unittest discover` | 35/35 OK ✓ |
| 配置等价 | 纯 v4 vs 合并版，25 项对比 | 全部一致 ✓ |
| 功能在位 | 实例化检查 | v3 三腿 ✓ v4 概要 ✓ v4 锚点 ✓ 脱敏 ✓ |
| 实体抽取 | `extract_entities("上次和图图聊 memobase")` | `['图图', 'memobase']` ✓ |
| recall 双形态 | 家里 vs 公开 | 「小爱正在回忆 💭」vs「memobase 🧠」✓ |

**生产目录 `~/.hermes/plugins/memobase` 全程零改动**（本次全在 `/tmp/mb-merge-sim` 模拟）。
