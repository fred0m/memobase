# Hybrid retrieval reference

A client-side pattern for combining keyword and vector recall, used with
Memobase's existing search endpoints. The server is untouched; everything
below can run in the calling application.

## Architecture

```
query
  ├─ vector leg: GET /users/event/search/{user_id}        (pgvector cosine, event ids)
  ├─ keyword leg: local BM25 index over GET /users/event/{user_id} (event ids, TTL cache)
  └─ RRF fusion (k=60) → top events
        └─ optional reranker pass (only when coarse ranking is ambiguous)
             → top events → build the recalled context
```

Both legs operate on the **same id space** (event ids) so that RRF fusion is
meaningful — fusing different object types (e.g. gist ids vs event ids)
degrades to a simple interleave.

## Why a keyword leg

Vector similarity misses exact-keyword matches: domain names
(`git.example.com`), file names (`GOALS.md`), parameter names and code tokens
often score low semantically even though they are the exact term the user
asked about. A BM25 leg over the event summaries catches those.

Practical implementation notes:

- Tokenize mixed Chinese/English text as **CJK unigram+bigram per contiguous
  chunk** plus latin words (strip trailing `._-`), no dictionary needed.
- Cache the full event list locally (e.g. TTL 5 min) instead of indexing per
  query; fetch it from `GET /users/event/{user_id}?topk=1000`.
- Fetch the event list **outside** the cache lock; on fetch failure back off
  briefly instead of retrying every call.

## RRF fusion

Reciprocal Rank Fusion: for each ranked list, add `1/(k + rank)` per id,
then sort by accumulated score. With k=60 a document seen in both legs
accumulates twice — that is the "agreement bonus" that makes hybrid recall
robust.

> [!WARNING]
> **Threshold scale trap** (see also `reranker-selection.md`): with k=60 even
> a unanimous two-leg ranking leaves only ~0.0005 between rank 1 and 2, so
> absolute-score thresholds are meaningless. Compare *ratios*, or use
> disagreement/overlap signals instead.

## Suggested Server-Side Improvements

To move this pattern fully server-side, the search endpoints would benefit
from:

1. A keyword leg on the server (Postgres `tsvector`/`pg_trgm`), fused with
   the vector leg.
2. **`event_id` in the gist search response** —
   `GET /users/event_gist/search/{user_id}` currently returns gist ids only,
   so clients cannot map results back to events for cross-endpoint fusion.