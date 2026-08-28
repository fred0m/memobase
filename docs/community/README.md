# Community Notes

Practical notes collected from running Memobase in a self-hosted deployment.
These are independent of the docs site build and are meant as field
experience for other users.

- [Embedding configuration](./embedding-configuration.md) — dimension
  matching, model selection, backfilling existing events
- [Reranker selection](./reranker-selection.md) — Qwen3-Reranker sizing and
  when to pay for a rerank pass
- [Hybrid retrieval reference](./hybrid-retrieval-reference.md) — BM25 +
  vector RRF + conditional rerank as a client-side pattern