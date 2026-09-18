# FR-019 锚点保底注入 · 独立评审报告

- 评审人：小三月（独立评审，与设计者图图/实现者 agy/评测执行者小爱无协作）
- 日期：2026-09-01
- 评审对象：
  1. 设计文档：`~/hotProject/Hermes/architect/designs/2026-09-01-memobase-summary-anchor-backfill-design.md`
  2. 实现 diff：`~/.hermes/plugins/memobase/`（feature/summary-layer-v4，uncommitted，基于 655dd1e）
  3. 评测脚本：`~/hotProject/memu-research/eval-out/eval_td_regression.py`（新增锚点推演段）
  4. 评测数据：`td-regression-20260830-1724-hybrid.json` / `-nosummary.json` + `queries.yaml`（新增 #37-40）+ `events-snapshot-20260901.json`
- 边界：只读评审，不改文件，不 SSH，不跑烧钱命令
- 评审重点：①实现与设计一致性（伪码逐段对照）②评测与生产行为对齐 ③锚点机制（预算/去重/fail-open）④评测结论（0 触发/temporal 归因/gold 标注）⑤边界情况

---

## Summary

**总体判断：实现质量高于设计文档，评测数据可信度打折。**

- 插件实现（约 35 行核心逻辑 + `date_for`/`events_for_date` 辅助）与设计意图高度一致：纯追加不改排名、锚点不进 rerank、`time_window is None` 幂等门控、四项配置键名/env/默认值逐一兑现。fail-open 实现强于伪码（三重复份回滚 vs 伪码口头承诺）。**未发现会导致生产故障的代码 bug。**
- 但三个层面存在问题：
  1. **评测可复现性已断裂（P0）**——两份输出实际用的是 284 事件新快照，而脚本常量与 queries.yaml 元数据仍指向 197 事件旧快照（sha1 `7651ebec…`）。今天重跑会得到完全不同的数字。
  2. **「0 触发」结论不能外推到生产（P1）**——harness 的触发判定与生产 rows 口径不一致；anchor 场景 4 条查询没有一条真正命中保底路径，验收门空转。本次评测对「锚点有没有用」零信息量，只证明了「零回归」。
  3. **设计伪码存在不可执行级别的错误**（rank_map 解包会崩、fused 外排序四个口径各说各话）——compressor-v3 的「伪码与实测不一致」教训在本设计上以更小规模复现。
- temporal 回归（MRR 0.667→0.583）归因**正确**：hybrid 与 nosummary 两臂逐查询完全一致，回归来自语料扩容（197→284 事件，8/30 当日 30→58）而非 FR-019。但脚本门禁打印口径（对 v3_base 比较打 ❌）会误导读者。

---

## Findings

### P0

#### P0-1 评测运行与脚本/元数据快照脱节，可复现性断裂

- **位置**：`eval_td_regression.py` L27（`SNAPSHOT = events-snapshot.json`）；`queries.yaml` L6-9（`version: 2`、`snapshot: events-snapshot.json`、sha1 `7651ebec…`）；产出 `td-regression-*-hybrid/nosummary.json`（9/1 15:35 / 15:45）
- **证据**：#37 的 gold（2850094f / 55e7f2f5 / 9d92ee4c，全部为 8/31–9/1 事件）**不在旧快照**（197 事件，无 8/31、9/1 数据），但输出 mrr=1.0 且 top3 = [2850094f, f57bff69, 9d92ee4c]——运行时语料必然包含新快照的 284 事件（sha1 `ac9bf349…`，未被任何配置引用）。而 `events-snapshot.json` 本体 mtime 仍是 8/30。
- **建议**：把新快照路径 + sha1 写进 queries.yaml 头部（升 v3），重跑留档。设计 §7.3 承诺的「快照 sha1 纪律」在本次验收数据上不成立——不修复的话，所有结论不可审计。

### P1

#### P1-1 评测 harness 的触发判定与生产 rows 口径不一致，「0 触发」不能严格外推

- **位置**：eval L274-289（事件注入循环）vs 插件 `__init__.py` L790-802
- **证据**：生产 `injected_dates` 按 **rows** 计算（`fused[:hybrid_keep*2]` 取、文本去重、**封顶 hybrid_keep=10 个事件**）；评测无 10 事件封顶、按行回溯匹配 event_events、无跨段 seen_lines 去重。锚点日事件排在第 11 名开外时：生产判 missing → 触发保底；评测大概率已注入该日某行 → 不触发。
- **建议**：harness 补 hybrid_keep 截断、改用 rows 口径；或直接 importlib 加载生产 `_call_context_hybrid` 离线跑（设计 §10 已有先例）。这是「评测 harness 与生产不一致最致命」的当代案例。

#### P1-2 锚点候选排序四个口径互相矛盾（伪码 / 设计正文 / 实现 / 评测）

- **位置**：
  - 设计 §4.2 伪码 L206：`rank_map.get(e, 10_000)` 稳定排序 → fused 外**当日时间升序**
  - 设计 §4.2 正文 L201：「fused 外按当日时间**倒序**」
  - 实现 `__init__.py` L848-850：fused 外**时间降序**（最新优先）
  - 评测 L319-322：fused 外按**快照 JSON 插入序**
- **证据**：当同一锚点日有 >2 个 fused 外事件时，四者会选出不同的事件注入。本次 0 触发所以未暴露，但机制一旦上线触发就是真实分歧。
- **建议**：定死一个口径（建议实现的时间降序 = 最新优先），同步回改设计伪码与评测脚本。

#### P1-3 anchor 场景基准没有打到机制，验收门空转

- **位置**：queries.yaml #37-40；设计 §7.2 门 2 / 门 3
- **证据**：
  - #37：锚点日 8/31、9/1 均已有事件进注入集（top3 含两个 9/1 事件）→ missing 为空
  - #38 / #40：gold 天然 top1
  - #39：gold 日 8/29 **不在语义 top3**（summary_days = 8/30、8/31、9/1）——语义路信号本身 miss
  - 设计预测的受益查询 #5 / #12 在新语料下 mrr/rec 全 1.0 **自愈**（#12 语义命中 8/28 gold 日且事件 top1），锚点不再触发
  - 门 2「#5/#12 不回退（+0.333/+0.500）」与门 3「触发率 ≤1/3」空转通过
- **建议**：补「构造性触发」用例（把 gold 事件从三腿候选剔除后验证保底追加与 Recall 提升），或对注入块做函数级单测。当前评测只回答了「无害」，没回答「有用」。

#### P1-4 temporal 门禁打印口径误导

- **位置**：eval L412-427（v3_base 对照表）
- **证据**：temporal MRR 0.583 vs v3_base 0.667 → Δ-0.083 打 ❌；但 nosummary 臂同样 0.583，逐查询两臂完全一致（#19 1.0→0.5、#20 1.0→0.5、#22 rec 减半两臂同步）。回归真实来源：语料 197→284（8/30 当日事件 30→58，全落在 temporal 增益窗内）+ EVAL_NOW 冻结在 8/30。按设计门 1 的正确定义（hybrid vs nosummary ≥ -0.05），实际 Δ=0 **通过**。
- **建议**：汇报口径统一为双臂同语料对比；v3_base 表在新语料上重建，否则每次跑都假报警。

#### P1-5 生产「事件腿全空」时锚点被短路

- **位置**：`__init__.py` L803-806（rows 为空 → 提前 return，跳过锚点块）
- **证据**：设计 §4.4 边界矩阵未覆盖此路径。纯语义查询三腿全 miss 但语义概要命中时，恰是锚点最该兜底的场景，实现却直接返回纯概要上下文。
- **建议**：认可设计意图就把锚点块挪到 early-return 之前；接受现状就在边界矩阵补一行说明。

### P2

#### P2-1 设计伪码三处不可执行级别错误

1. §4.2 `rank_map = {eid: i for i, (eid, _) in enumerate(fused)}`——生产 `fused` 是 `List[str]`（L729），照抄会 ValueError；实现静默修正未回写文档
2. 伪码 `text_for(eid).splitlines()` 无 None 检查；实现补了 `if not txt: continue`
3. §4.1 `str(s["summary_date"])` 直取键与 `.get` 过滤写法混用

**建议**：以实现为准回改设计文档。

#### P2-2 `events_for_date` 新增 str 分支未列入设计改动范围

- **位置**：`hybrid_retriever.py` L624-628
- 锚点传日期字符串的必要使能改动，设计 §4.5 / 文件清单没提。向后兼容、风险低，spec 登记时补上。

#### P2-3 生产锚点观测盲区

- **位置**：`__init__.py` L886-891（`logger.info` 在 `if missing:` 内）
- missing 为空时零日志，无法区分「锚点日均已覆盖」和「代码没执行」。agent.log 当前 0 条 anchor 日志，连新代码是否被运行中的 agent 加载过都无法确认。
- **建议**：锚点提取处加一条 debug 级日志；§7.4 一周观察依赖这个区分。

#### P2-4 gold 标注含 meta 事件与二次提及事件

- **位置**：queries.yaml #37
- 9d92ee4c 是「召回观察」meta 事件；55e7f2f5 主体是「最喜欢你了」（尾带清理二次提及）。主信息源是 2850094f。rec=0.667 缺的 1/3 是标注造成的 ceiling，不是召回缺陷。
- **建议**：gold 分 primary/secondary 或剔除 55e7f2f5。

#### P2-5 评测与生产的存量口径漂移（直接影响锚点计量）

1. **事件时间源**：评测用 event_tip「提及于」正则，生产用服务端 created_at（实测 3/275 事件两源差一天，如 dcd11910 提及 8/31 / 创建 9/1——边界事件可翻转 missing 判定）
2. 评测行无 `- ` 前缀、无跨段行去重 → `used` 系统性偏低
3. temporal age 评测用小数天、生产用整数日差
4. **锚点追加预算**：评测按整事件文本判定，生产按行截断（可部分注入）
5. 评测脚本硬编码 SiliconFlow API key（安全卫生）

**建议**：至少对齐 ①④。

#### P2-6 anchor 场景未进门禁打印表

- **位置**：eval L415 scene 列表缺 `"anchor"`，控制台看不到该场景行（JSON 里有）。

### 做对了的（对照 compressor-v3 教训的正分项）

- `budget_exceeded` 重构（early-return 改 break）**行为逐字节等价**——已核对 `and lines`、seen_lines 时序等细节，无扰动
- fail-open 实现强于伪码（orig_lines / orig_used / orig_seen_lines 三重复份回滚）
- 锚点不进 rerank、不改排名主体、`time_window is None` 幂等门控——设计 §4.4 / §4.5 承诺全部兑现
- 配置项 4 个的键名 / env / 默认值与设计 §4.1 完全一致；评测 temporal / rerank 常量与生产默认值（1.6 / 15 / 8 / 900）对齐
- 双臂（hybrid vs nosummary）实验设计本身是对的——temporal 归因能做对全靠它

---

## 结论速览

| 维度 | 判定 |
|---|---|
| 实现与设计一致性 | 基本一致；实现修复了伪码 bug 但未回写文档（P1-2 / P2-1） |
| 评测与生产对齐 | 不对齐：rows 口径 / 时间源 / 预算判定 / 排序口径四处漂移（P1-1 / P2-5） |
| 锚点机制本身 | 预算双封顶、去重三保险、fail-open 均落实；rows 全空短路是缺口（P1-5） |
| 「0 触发」结论 | harness 口径下成立，但外推不到生产；anchor 基准空转（P1-1 / P1-3） |
| temporal 回归归因 | 正确（语料扩容，两臂一致）；门禁打印口径误导（P1-4） |
| 新增查询 gold | 大体合理，#37 含 meta/二次提及事件（P2-4） |
| 快照纪律 | 断裂（P0-1） |

**一句话结论：代码可以合，但验收先欠着。** P0-1 的快照对账不修，这次评测的任何数字都立不住；P1-3 不补构造性用例，FR-019 就是个「上线后不知道有没有用、坏了也不知道」的机制。

---

## Unverified

1. **运行时快照切换机制**：只能从输出反推 15:35 / 15:45 两次运行用了 284 事件语料；是临时改 SNAPSHOT 常量后回滚、还是临时换文件内容，无法确认。
2. **两臂间 API 漂移**：hybrid（15:35）与 nosummary（15:45）间隔 10 分钟，vec / rerank API 状态假设一致，未逐条比对 rerank 触发记录。
3. **未执行任何评测/生产代码**（评审边界）：生产侧「锚点会不会触发」的结论基于静态代码对照 + harness 数据推演，非重放验证。
4. **memobase-server 语义路返回结构**（summary_date 格式、errno / data 包装）按插件代码假设，未直连验证。
5. **agent.log 零 anchor 日志的含义**：无法区分「没触发」和「插件没被加载」（代码未提交，运行中的 agent 可能还在用旧代码）。
