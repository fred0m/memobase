# Feature Specification: memobase 概要层（日概要文档层 + 混合召回改造）

**Feature Branch**: `feature/summary-layer`（服务端）/ `feature/summary-layer-v4`（插件）

**Created**: 2026-08-30

**Status**: Draft

**Input**: 概要层落地设计 `~/hotProject/Hermes/architect/designs/2026-08-30-memobase-summary-layer-design.md`（沫沫 2026-08-30 拍板：改造 memobase 为主，召回形态 = 文档层 + 少量高相关事件层，细节 drill-down；执行流程：图图 spec → 小爱控制进度 → agy 实现 → agy 独立评审）

**数据依据**（全部出自 eval-out/，验收标准引用）：
- 第一轮 memobase v3 基线：all MRR 0.769 / Recall 0.771（`eval-results-final.json`）
- 第二轮文档形态基线：doc 粒度 all MRR 0.894 / Recall 0.972，事件粒度 ev MRR 0.110（`eval-memu-docs-results.json`）
- 查询集：36 条五场景标注 + gold（`queries.yaml`，快照 sha1 `7651ebec…`）
- 桌面组合分析：组合下界 all 0.911；反向风险查询 5 条（#5/#16/#19/#25/#36）；时间直通「今天」类 gold 覆盖 1.00（设计 §A）

---

## User Scenarios & Testing *(mandatory)*

说明：本 feature 的「用户」是家庭记忆系统（memobase + Hermes 插件）的使用与运维者：沫沫（拍板/验收）、小爱（进度控制/评测执行）、agy（实现/评审）。用户故事按可独立交付排序。

### User Story 1 - 日概要的存储与自动生成（Priority: P1）

memobase 服务端新增概要层：每天的对话事件自动聚合成一份「日概要」文档（拼接版，无 LLM），事件 flush 提取成功后异步更新当日概要。任何人问「今天/最近我们做了什么」时，系统有按天组织的文档层可供检索。

**Why this priority**: 文档层是本次改造的地基（R2 已验证 doc 粒度 0.894/0.972 全面领先），没有存储与生成，后续召回/drill-down 无从谈起。

**Independent Test**: 服务端部署后，向测试用户插入两条含明确信息的对话 blob → flush → `GET /users/summary/list` 返回当日概要，内容含当日事件行且不含「无新信息」行；重复 flush 后概要幂等更新（不产生第二条）。

**Acceptance Scenarios**:

1. **Given** 用户当日已有 N 条事件入库，**When** 调用概要列表接口按日期查询，**Then** 返回 1 条该日概要，content 为当日全部有效事件行的顺序拼接，event_ids 记录全部来源事件 id
2. **Given** 当日发生新的 flush 且产生新事件，**When** flush 流程完成，**Then** 当日概要在无人工干预下被重算更新（同日仅一条，内容含新事件）
3. **Given** 概要重算过程抛出异常，**When** flush 主流程执行，**Then** flush 本身不受影响（事件/画像正常入库），重算失败仅记日志（fire-and-forget）
4. **Given** 单日事件文本总量 15000+ 字符，**When** 生成概要 embedding，**Then** embedding 请求输入被截断在 embedding_max_token_size 以内，服务不报错
5. **Given** 概要表为空（新装/回滚后），**When** 旧代码或插件访问现有事件/画像接口，**Then** 行为与改造前完全一致（新表对旧路径零影响）

### User Story 2 - 混合召回：文档段 + 事件段分预算注入（Priority: P1）

Hermes 插件每轮自动召回改为两段结构：文档段（1-2 个日概要，语义检索 + 时间词直通双路）+ 事件段（现有三腿 RRF + temporal boost + rerank 门控，行为不变）。两段独立预算、跨段行级去重。

**Why this priority**: 这是沫沫拍板的核心召回形态；桌面组合分析显示组合下界 0.911 > 事件腿单独 0.771 / 文档腿单独 0.894，12 条查询严格增益。

**Independent Test**: flag 开启后，向插件发「最近我们在搞什么新系统」类查询，注入上下文出现「近期概要」段且随后有「过去事件」段；发「我之前让你记的那个生日是哪天」类查询（文档弱事件强），事件段行为与 v3 一致（T-D 回归验证）。

**Acceptance Scenarios**:

1. **Given** 概要表已回填且有当日/近期概要，**When** 召回查询命中概要（语义路或时间直通路），**Then** 注入文本在「过去事件」段之前包含「近期概要」段，头部带日期与来源提示（当日事件数 + 可按日期查询细节）
2. **Given** 查询含时间词（如「今天/昨天/最近」），**When** 时间窗解析命中且窗口内日期存在概要，**Then** 直通路拉取窗口内日期的概要，不依赖向量相似度
3. **Given** 概要语义命中与时间直通命中不一致，**When** 组装文档段，**Then** 两路并集去重后按相关度/窗口顺序取不超过 2 个文档
4. **Given** 文档段无命中（表空/当天未生成/两路都空），**When** 召回执行，**Then** 文档段预算让渡给事件段，整体行为回退 v3（fail-open，无报错）
5. **Given** flag `summary_enabled=false`（默认），**When** 任意查询召回，**Then** 输出与 v3 逐字节一致（灰度前提）
6. **Given** 文档段与事件段命中行有重复文本，**When** 注入组装，**Then** 跨段行级去重生效（同一行文本只出现一次）
7. **Given** 时间窗解析发生，**When** 事件段 temporal boost 计算，**Then** 文档腿与事件腿共用同一次窗口解析结果（一次解析两处使用，不重复计算，语义一致）

### User Story 3 - drill-down：按日期主动查询事件细节（Priority: P2）

概要段是压缩视图，回答不了「具体哪个事件」。Hermes 需要能在看到概要后按日期拉取当日全部事件行。

**Why this priority**: R2 事件粒度全 0（ev MRR 0.110，26/36 条为 0）证明文档层定位不到行；概要段头部提示 + 工具参数给 agent 明确的钻取路径。

**Independent Test**: 调用 `memobase_search(query, date="2026-08-29")`，返回该日全部事件行（受 max_length 截断，不受 900 注入预算约束）；不带 date 参数行为不变。

**Acceptance Scenarios**:

1. **Given** EventStore 已缓存事件（含时间戳），**When** `memobase_search` 带 `date` 参数，**Then** 仅返回该日期的事件行，按时间顺序，服务端零改动（本地过滤）
2. **Given** `memobase_search` 不带 date 参数，**When** 调用，**Then** 行为与 v3 完全一致（向后兼容）
3. **Given** 文档段注入发生，**When** 渲染概要头部，**Then** 包含「当日 N 事件，细节可查 memobase_search date=…」提示；system_prompt_block 含概要段来源与钻取指引一句话

### User Story 4 - 历史数据回填（Priority: P2）

存量 17 天（2026-08-09~08-30，197 事件）回填日概要，让概要层上线即有全量数据，T-D 回归才有文档段可用。

**Why this priority**: 没有回填，T-D 回归中文档段恒空，验收无法进行。

**Independent Test**: 运行回填脚本后 `GET /users/summary/list` 返回 17 个日期的概要；抽查 2026-08-29（66 事件）与 2026-08-30 概要内容与事件行一致。

**Acceptance Scenarios**:

1. **Given** 生产库中用户有历史事件，**When** 运行回填脚本，**Then** 每个有事件的日期生成一条 kind=daily 概要，event_ids 完整，embedding 已写入（NULL 不允许）
2. **Given** 回填中断后重跑，**When** 脚本执行，**Then** 幂等（已存在的日期重算覆盖，不重复、不报错）
3. **Given** 回填完成，**When** 概要语义检索接口用「memU 评测」类查询测试，**Then** 相关日期概要排前（embedding 有效性验证）

### User Story 5 - LLM 压缩版概要与 A/B（Priority: P3）

拼接版概要单日可达 15000+ 字符，注入靠行选截断。可选的 LLM 压缩版（每日一次 cron 生成 ~800-1500 字符叙事）是否替换拼接版，由 T-C A/B 用数据决定，不拍脑袋。

**Why this priority**: 依赖 US1/US2/US4 落地后才能跑；且拼接版已是可交付形态，LLM 版是优化项。

**Independent Test**: cron 每日调用 rebuild 接口生成 LLM 版（kind 不变或并存标记），36 条基准上对比两版 doc 指标 + 注入预算占用。

**Acceptance Scenarios**:

1. **Given** 拼接版概要存在，**When** 调用 rebuild 接口带 style=llm，**Then** 服务端用现有 LLM 通道生成压缩概要并覆盖/并存写入，过程复用 flush 同款 LLM 调用封装
2. **Given** LLM 压缩版与拼接版并存，**When** 跑 36 条基准 A/B，**Then** 结果落档：LLM 版 doc 指标不降 + 注入预算占用减半 → 替换；否则维持拼接（判定标准见 SC-006）
3. **Given** LLM 通道经 9router，**When** 生成前，**Then** combo 映射确认避开 cbcn 渠道（system prompt 替换毁提取的已知坑），确认步骤写入运维手册

### User Story 6 - 灰度发布与回归验收（Priority: P1）

全部改动在 feature 分支开发、flag 默认关闭合入、T-D 回归通过后才开 flag；每步独立可回退。这是沫沫的规矩，也是本 feature 的验收闸门。

**Why this priority**: 记忆系统是生产依赖（每轮对话自动召回），回归风险直接伤日常体验；R1 #17 教训是单条查询可振荡 ±0.5 MRR，不跑回归不知道改动是赚是赔。

**Independent Test**: T-D 回归 36 条在混合链路上重跑，结果与判定标准对比；flag 开关前后各跑一次冒烟查询对比注入内容。

**Acceptance Scenarios**:

1. **Given** 服务端与插件改动全部完成，**When** T-D 回归执行（36 条基准、真实预算约束、评测脚本加概要段渲染），**Then** all MRR ≥ 0.85 且任一场景不低于 v3 基线 −0.05（auto 0.708 / active 0.823 / temporal 0.667 / entity 1.000 / vague 0.575 各自对比），通过才允许开 flag
2. **Given** T-D 任一场景回归，**When** 判定，**Then** 不开 flag，修完重跑
3. **Given** flag 开启后一周观察期，**When** 检查注入日志，**Then** 文档段命中率、预算占用、drill-down 调用率有记录（观察项，非阻断）
4. **Given** 需要回退，**When** 执行回退路径，**Then** 插件侧 flag=false 秒级回 v3 行为；服务端侧 git checkout main + rebuild；数据侧 pg_dump 恢复（migration 前 pg_dump 是部署前置条件）

### Edge Cases

- 单日事件量为 0（无对话日）：不生成该日概要（列表接口自然缺省），时间直通窗口内无该日期即跳过
- flush 与概要重算并发：概要重算为幂等 upsert（唯一约束 user_id+project_id+summary_date+kind），最后写入者胜，无半写状态
- 概要 embedding 服务（硅基流动）不可用：embedding 写 NULL，注入照常（文档段检索跳过 NULL），下次重算补齐；不阻断 flush
- 「昨天」口径错位（事件 created_at 时区与本地日期差一天）：时间直通用相对天数窗（parse_time_window 现有语义），不用日历映射；错位风险记录在案（R2 #18 实测），超窗时语义路兜底
- 查询时间窗内日期部分有概要部分没有（当日未 flush）：有则注入、没有的日期不报错
- EventStore 缓存 TTL（300s）内新事件未入缓存：drill-down 按日过滤可能滞后一个 TTL，可接受（与 v3 缓存语义一致）
- 超长查询（接近 1500 字符截断上限）含多个时间词：parse_time_window 现有「最具体窗口优先」规则决定，两腿共用结果

---

## Requirements *(mandatory)*

### Functional Requirements

**服务端（memobase fork，分支 feature/summary-layer）**

- **FR-001**: 系统 MUST 新增表 `user_summaries`，schema 为：`id UUID PK`、`user_id UUID`、`project_id VARCHAR(64) 默认 __root__`、`summary_date DATE`、`kind VARCHAR(8) 取值 daily|weekly|monthly`、`content TEXT`、`event_ids JSONB`（来源事件 id 列表）、`embedding vector(1536) nullable`、`created_at/updated_at TIMESTAMP`；唯一约束 `(user_id, project_id, summary_date, kind)`；索引 `(user_id, project_id, summary_date)` 与 `(user_id, project_id, kind)`；外键 user_id → users(id, project_id) ON DELETE CASCADE。迁移 MUST 用 alembic revision（纯加表，不改任何现有表）
- **FR-002**: 系统 MUST 提供 `POST /api/v1/users/summary/{user_id}` upsert 路由：body 含 summary_date/kind/content/event_ids，按唯一约束幂等（存在则更新 content/event_ids/embedding，不存在则插入）；MUST 同步计算并写入 embedding（复用现有 get_embedding 封装，模型与事件 embedding 相同）；embedding 输入 MUST 截断：content 取「主题行（每事件首行）+ 前 4000 字符」
- **FR-003**: 系统 MUST 提供 `GET /api/v1/users/summary/{user_id}` 列表路由：参数 start_date/end_date/kind（默认 daily），返回时间正序概要列表
- **FR-004**: 系统 MUST 提供 `GET /api/v1/users/summary/search/{user_id}` 语义检索路由：参数 query/topk（默认 3）/kind，pgvector 余弦相似度排序，跳过 embedding IS NULL 行，返回带 similarity
- **FR-005**: 系统 MUST 提供 `POST /api/v1/users/summary/{user_id}/rebuild` 路由：参数 date/kind/style（concat|llm，默认 concat）。concat：拉取该日全部事件（user_events 按日过滤）→ 过滤空行与「无新信息」行 → 按时间序拼接 → upsert。llm：在 concat 结果上走一次 LLM 压缩（复用 llm_complete，摘要 prompt 独立成文件）→ upsert。响应返回 event_ids 数量
- **FR-006**: flush 管线 MUST 在 `handle_session_event` 成功后 fire-and-forget 触发当日 concat rebuild（asyncio.create_task 或等价，异常仅记日志，不 await、不影响 flush 返回）；当日无有效事件行时不写入
- **FR-007**: 系统 MUST 从部署配置模板删除 `enable_event_summary` 行（0.42 服务端 Config dataclass 无此字段，写入也不生效的死配置；`deploy/config.yaml` 与 README 同步清理）
- **FR-008**: 概要生成 MUST 丢弃「无新信息」类空行（概要层自身的清洗职责；事件入库侧清洗不在本期范围）

**插件（~/.hermes/plugins/memobase/，分支 feature/summary-layer-v4）**

- **FR-010**: 插件 MUST 新增配置项 `summary_enabled`（memobase.json / env `MEMOBASE_SUMMARY_ENABLED`，默认 false）、`summary_budget_tokens`（默认 350）、`event_budget_tokens`（默认 850）、`summary_topk`（默认 2）、`summary_semantic_topk`（默认 3），解析遵循现有 `_as_bool`/`_cfg_int` 防御模式
- **FR-011**: flag 开启时 `_call_context_hybrid` MUST 在事件段之前组装文档段，双路检索：(a) 语义路 `GET /users/summary/search/{uid}`（query 同事件腿，topk=summary_semantic_topk）；(b) 直通路：`parse_time_window(query)` 一次解析，窗口换算为日期集（本地时区），`GET /users/summary/list` 拉取。两路按 summary_date 去重合并，最多取 summary_topk 个文档
- **FR-012**: 直通路与事件腿 temporal boost MUST 共用同一次 `parse_time_window` 调用结果（不重复解析、语义一致）
- **FR-013**: 文档段注入格式 MUST 为「## 近期概要：」标题 + 每文档「【M/D 概要】（当日 N 事件，细节可查 memobase_search date=YYYY-MM-DD）」头部行 + 概要 content 的行级渲染；行级渲染与事件段共用同一渲染器；文档段预算 summary_budget_tokens 用尽即止
- **FR-014**: 事件段 MUST 维持 v3 行为不变：三腿 RRF（vec/bm25/entity）、temporal boost、rerank 门控、行级注入逻辑全部不动；事件段预算从 `_hybrid_event_budget_tokens` 改为 `event_budget_tokens`（默认 850，值可配回 900）
- **FR-015**: 文档段与事件段 MUST 共享一个行级 seen 去重集合（文档段先占，事件段跳过已注入行）
- **FR-016**: 文档段任何异常（接口失败/表空/解析失败）MUST fail-open：日志 warning + 文档段空缺 + 预算让渡事件段，整体不报错；flag 关闭时代码路径零执行
- **FR-017**: `memobase_search` 工具 MUST 新增可选参数 `date`（YYYY-MM-DD）：带参时从 EventStore 本地按日过滤（created_at 日期匹配），返回当日全部事件行按时间正序（受 max_length 截断，默认 2000，不走 900 注入预算）；EventStore 未命中该日或缓存未刷新时按现有 TTL 语义处理；不带 date 参数行为与 v3 逐字节一致
- **FR-018**: `system_prompt_block` MUST 增加一句：概要段来自日级记忆压缩，需要具体事件细节时可按日期用 memobase_search 查询

**回填脚本**

- **FR-020**: 仓库 `scripts/backfill_summaries.py` MUST 实现：按日扫描存量 user_events → 对每个有事件的日期调 rebuild 逻辑（服务端函数直调或 REST，二选一在 plan 定）→ 幂等可重跑 → 输出统计（日期数/事件数/embedding 成功数）。历史数据回填 17 天 MUST 在 5 分钟内完成（embedding 走硅基流动批量）

**灰度与验收**

- **FR-030**: 服务端改动 MUST 全部在 `feature/summary-layer` 分支（memobase fork，推 git.momo anon/memobase），部署副本 dockercenter:~/memobase/ 用 git pull + rebuild；migration 前 MUST pg_dump 全量备份 memobase 库并存 NAS
- **FR-031**: 插件改动 MUST 全部在 `feature/summary-layer-v4` 分支，flag 默认 false 合入；插件仓库当前无 remote，MUST 推一份到 git.momo 做备份
- **FR-032**: T-D 回归 MUST 用 R1 评测框架（36 条 + importlib 加载生产代码 + EVAL_NOW 冻结），评测脚本增量改动：概要段渲染 + 分段预算；结果 JSON 落 eval-out/，判定标准见 SC-001
- **FR-033**: 回退路径 MUST 可执行并演练：插件 flag=false 即回 v3；服务端 checkout main + rebuild；数据 pg_dump 恢复；user_summaries 表留存对旧代码无副作用（可不清除）

### Key Entities *(include if feature involves data)*

- **UserSummary（日概要）**：一个用户一个日期一份（kind 内唯一）。属性：summary_date（本地日期）、kind（daily/weekly/monthly，本期仅 daily）、content（当日有效事件行的时间序拼接，或 LLM 压缩版）、event_ids（来源 user_events id 列表，drill-down 与审计锚点）、embedding（1536 维语义向量）。关系：user_id 多对一 users；event_ids 逻辑引用 user_events（软引用，事件删除不级联——概要保留生成时点快照）
- **概要段（召回输出形态）**：文档段的注入单元。属性：日期、来源事件数、行集（content 行级渲染）、钻取提示。约束：最多 summary_topk 个文档、summary_budget_tokens 预算内
- **事件段（召回输出形态）**：v3 现有行级注入段，行为不变。约束：event_budget_tokens 预算内、与文档段跨段去重

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001（T-D 回归闸门）**: 36 条基准在混合链路（flag 开、真实预算 350/850）上重跑：all MRR ≥ 0.85；且 auto ≥ 0.658（0.708−0.05）、active ≥ 0.773、temporal ≥ 0.617、entity ≥ 0.950、vague ≥ 0.525（各自 v3 基线 −0.05）。同时 all Recall ≥ 0.85（v3 0.771 的增量验证）。任一不达 → 不开 flag
- **SC-002（文档段增益兑现）**: T-D 中文档段命中的查询（至少 12 条已知增益查询：#1/#2/#6/#10/#14/#17/#22/#23/#29/#32/#33/#34）gold 覆盖不劣于各自 v3 Recall（组合下界 0.911 的端到端验证）
- **SC-003（时间直通信道）**: 时间词查询（#17/18/21/22 四条）在概要表回填后，文档段注入包含 gold 所在日概要（桌面验证的 1.00/1.00/1.00 覆盖在真实链路兑现；#21「今天」类从 v3 的 0 变为概要命中）
- **SC-004（预算与大文档）**: 2026-08-29（66 事件/15399 字符）作为 worst case：文档段注入 ≤ 350 字符预算、embedding 输入不超 8192 token 上限、事件段照常注入（分预算互不挤占）
- **SC-005（回填效率）**: 17 天历史回填 ≤ 5 分钟，重跑幂等（日期数不增）
- **SC-006（T-C 判定，P3 项）**: LLM 压缩版 A/B：36 条 doc 指标（MRR/Recall）不低于拼接版 −0.02 且文档段平均预算占用下降 ≥40% → 替换；否则维持拼接版
- **SC-007（灰度零破坏）**: flag=false 时 36 条基准 MRR/Recall 与 v3 基线完全一致（逐条 diff=0）；flag=true 后 fail-open 路径可用（概要接口断连时注入不报错、行为回退 v3）

## Assumptions

- 单用户（沫沫 user）生产场景，多用户（clawra）不受影响但 schema 天然支持
- 概要拼接版的「行」即 event_tip 行（LLM 摘要产物），非原始对话——质量基线与 R2 一致
- embedding 服务沿用硅基流动 Qwen3-Embedding-4B@1536（与事件同模型同维度，表列 vector(1536) 匹配）
- LLM 通道沿用 9router memobase-use combo；执行 LLM 概要（T-C）前确认 combo 无 cbcn（memobase-config skill 排查命令）
- 周/月概要（kind 扩展位）本期不实现，仅 schema 预留
- 事件入库侧空事件清洗、rerank 门控去二值化是 v4 独立项，不在本 spec 范围（设计 §5.5 已划界）
- 画像层、查询改写不动（设计 §8）
- 36 条基准样本量有限（单场景 6-8 条），基准扩到 60+ 条与 T-D 观察期同步推进，不阻塞本期验收
- EVAL_NOW 冻结与快照 sha1 纪律沿用 R1（queries.yaml 头部）

---

**验收流程备忘**（沫沫拍板）：本 spec → 小爱控制进度 → agy 按 spec 实现 → agy 独立评审 → T-D 回归（小爱执行）→ flag 开启观察一周 → T-C 定稿。设计文档（§5/§6/§7）是本 spec 的唯一权威依据，冲突时以 spec 为准并回写设计。