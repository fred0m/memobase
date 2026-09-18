# Embedding configuration

Field notes on enabling event embeddings (`enable_event_embedding`) in a
self-hosted setup.

## 1. `embedding_dim` must match the database column

`user_events.embedding` and `user_event_gists.embedding` are stored as
`vector(N)` where `N` comes from `embedding_dim` in `config.yaml` at table
creation time (default **1536**). If you later change `embedding_dim` to a
different value, inserts will fail with a dimension mismatch — you must either

- keep `embedding_dim` at the column's dimension, or
- `ALTER TABLE ... ALTER COLUMN embedding TYPE vector(M)` when the column is
  still all NULL (safe before backfill, no data lost).

Check the live column type with:

```sql
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
WHERE a.attname = 'embedding';
```

## 2. The server sends `dimensions=` on every embedding call

`src/server/api/memobase_server/llms/embeddings/openai_embedding.py` passes
`dimensions=CONFIG.embedding_dim` to the provider. Models that support
Matryoshka (MRL) truncation — e.g. Qwen3-Embedding — can therefore output
exactly the configured dimension, which lets you use a large model with a
small existing column. Verify the dimension round-trips on startup:

```
OpenAI embedding, <model>, document, 5/5
Embedding dimension matched: 1536
```

## 3. Model selection (Qwen3-Embedding family, measured on SiliconFlow)

| Model | MTEB Multilingual | Native dims | Latency (35-token input) | Notes |
|-------|:---:|:---:|:---:|-------|
| Qwen3-Embedding-0.6B | 64.33 | 1024 | ~0.15s | weakest, needs a column ALTER for 1536 |
| **Qwen3-Embedding-4B** | 69.45 | 2560 | ~0.17s | recommended: MRL @1536, zero schema change |
| Qwen3-Embedding-8B | 70.58 | 4096 | 0.2s–25s *flaky* | +1.1 MTEB over 4B but cold-instance latency spikes |

The 8B latency pattern was intermittent (0.2s when warm, 12–25s when the
provider's instance was cold) — poor fit for per-turn recall. 4B's MRL
support at 1536 dims matches the default column, so it is the practical
sweet spot.

## 4. Backfilling existing events

With `enable_event_embedding` flipped on, **existing rows keep `embedding`
NULL and are skipped by semantic search**. Backfill updates the column
directly:

1. Export `id` + text (`event_data->>'event_tip'`, or `gist_data->>'content'`) where
   `embedding IS NULL`
2. Embed in batches through the provider (use the same `dimensions=`)
3. `UPDATE <table> SET embedding = '<float list>'::vector WHERE id = ...`

Watch out for command-line length limits — pipe the SQL into `psql` via
stdin rather than `-c "<huge statement>"`.