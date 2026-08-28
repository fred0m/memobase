# Reranker selection

Notes on adding a cross-encoder rerank pass on top of Memobase's coarse
recall, including which model to pick.

## Why rerank at all

Vector retrieval (and any BM25-fused hybrid) returns a *rough* order.
A cross-encoder reranker scores `(query, doc)` pairs together and reliably
fixes the top few positions — useful when the coarse ranking is ambiguous.
For per-turn memory recall the candidates are few (10–20 short event
summaries), so latency is the main constraint.

## Qwen3-Reranker family (measured on SiliconFlow, 15 documents)

| Model | CMTEB-R (zh rerank) | MTEB-R (en rerank) | Latency (15 docs) | Verdict |
|-------|:---:|:---:|:---:|-------|
| Qwen3-Reranker-0.6B | 71.31 | 65.80 | ~0.30s | -4.6 on Chinese, skip |
| **Qwen3-Reranker-4B** | 75.94 | 69.76 | ~0.35s | recommended |
| Qwen3-Reranker-8B | 77.45 | 69.02 | ~0.55s | +1.5 CMTEB-R, 60% slower, weaker on English |

4B is the sweet spot for a Chinese-first memory store: it nearly matches 8B
on Chinese reranking, beats 8B on English, and is 60% cheaper in latency.
For this task's small candidate sets the practical ordering difference
between 4B and 8B was nil on our samples.

## When to actually call the reranker (conditional gating)

Reranking every recall is usually unnecessary. Three cheap, local signals
indicate the coarse ranking is unreliable:

1. **RRF top-1/top-2 ratio** — fused scores are nearly tied
   (`score1 / score2 < 1.01`).

   > [!WARNING]
   > Do *not* threshold the absolute score gap: in RRF with k=60 even a
   > unanimous ranking leaves only ~0.0005 between ranks 1 and 2, so an
   > absolute threshold like `0.002` fires on every query.
2. **Leg disagreement** — vector top-k and keyword top-k overlap ratio < 0.3
   (the two retrieval legs barely agree).
3. **Weak top similarity** — the best vector hit's similarity < 0.3.

Trigger the reranker (top 15 candidates → keep 8) only when any signal
fires; otherwise keep the coarse order. In practice: explicit, well-covered
queries skip the rerank, while vague or tied queries get the precise pass.
Fail open — if the rerank call errors, fall back to the coarse order.