# PR 草稿 v2（按沫沫反馈修订）

**目标仓库**: NousResearch/hermes-agent
**base**: main ← **head**: fred0m:add-memobase-to-plugin-catalog
**标题**: plugin-catalog: add memobase memory provider

---

## PR 正文（英文，最终稿）

Adds `plugin-catalog/memobase.yaml` per `plugin-catalog/README.md`.

**Plugin**: [hermes-memobase-provider](https://github.com/fred0m/hermes-memobase-provider)
implements the `MemoryProvider` ABC against a self-hosted
[Memobase](https://github.com/memodb-io/memobase) server — a user profile
plus an append-only event timeline, extracted offline by an LLM when the
buffer flushes.

**Retrieval.** Recall is not a single vector lookup. The plugin pulls the
event window from `/users/context` and runs a three-leg hybrid pass over it:

- BM25 over CJK-bigram + latin tokens (exact term match)
- Memobase's own embeddings (semantic match)
- an entity inverted index (paths, IPs, domains, camel-case identifiers,
  quoted strings, book titles)

The three ranked lists fuse with reciprocal rank fusion. Two optional passes
then refine the result: a temporal boost when the query carries a time
expression (今天 / 昨天 / 上周 / 3天前 / …), and a conditional cross-encoder
rerank that fires only when the fused distribution is low-discrimination —
that is, only when the ranking is too flat to trust.

**The reranker is opt-in and has no default endpoint.** It runs only when the
deployment sets both `rerank_base_url` and `rerank_api_key`. There is no
fallback host, so a default install talks to exactly one server: the user's
own Memobase instance.

**Capabilities.** The entry declares the two env vars the install prompts
for: `MEMOBASE_BASE_URL` (the user's own server, including the `/api/v1`
suffix) and `MEMOBASE_API_KEY` (its bearer token). The plugin registers no
tools, hooks, or middleware of its own — it contributes through the memory
provider lifecycle, and its two backstop tools surface through that ABC.

**Pinned SHA**: `4e8e447966acefaea372395e2db96ca0d0127f18`

**Validated at the pin**:
- `hermes plugins validate` → passed (manifest, declared-vs-registered
  capabilities, built-in tool collisions)
- `python -m unittest discover -s tests` → 35 passed
- no self-updating code (no GitHub fetch + file write in the catalog build)

---

## 修订记录（小爱自记，不发上游）

**v1 → v2 的改动**（沫沫 2026-09-16 反馈）：

1. **删掉硬编码的 rerank 默认端点**。原代码 `_rerank_base_url` 兜底写死
   `https://api.siliconflow.cn/v1`，只要有 key 就会用它。已改为默认 `""`，
   gating 变成「enabled && key && base_url」三者齐备才触发。现在「不提供上游
   地址、手动开启」是代码事实，不是文档承诺。
2. **PR 正文重写**。v1 那句「默认安装不向 Memobase 之外的任何第三方发请求」
   是把「有个第三方域名默认值」和「会外呼」混在一起说，读起来像免责声明，反而
   像在暗示插件会偷偷联系第三方。现在只陈述：reranker 必须显式配置两个值才
   启用，无兜底地址。
3. **cjk_known_names 保留**（沫沫确认）：公开包放空表 `[]`，部署侧自己填。
   不删，但要留这个结构给使用者。

**生产配置联动（重要）**：生产原本只设了 `MEMOBASE_RERANK_API_KEY`，端点靠
硬编码默认值在工作（日志实测 rerank 触发率 80%）。删默认值后会静默失效，所以
已往 `~/.hermes/memobase.json` 补了 `rerank_base_url`。备份：
`~/.hermes/backups/memobase.json.before-rerank-20260916-105459`。
