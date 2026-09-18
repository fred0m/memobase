<!--
上游 issue 草稿（memodb-io/memobase）
状态：待沫沫过目后再发
-->

**Title:** `merge_yolo`: parser silently drops every action when the model follows the prompt's own output template

---

## Summary

`parse_string_into_merge_yolo_action()` (in `memobase_server/prompts/utils.py`) discards **every** action line when the LLM writes `1. ACTION::APPEND` instead of `1. APPEND::APPEND`. The prompt (`zh_merge_profile_yolo.py` / `merge_profile_yolo.py`) is what leads the model there: its "output template" section literally shows `N. ACTION{tab}...`, and the model copies `ACTION` verbatim. The result is silent, large-scale loss of profile merges — no error, just one `No Corresponding Merge Action` warning per dropped item.

## Environment

- memobase server `0.0.42`, self-built image from this repo (server source under `src/server/api`)
- LLM backend: local `Qwen3-Coder-30B-A3B-Instruct` via llama.cpp (OpenAI-compatible endpoint)
- Also reproduced with cloud backends; the issue is backend-independent

## Root cause

`utils.py`:

```python
ORDER_LIST_PATTERN = r"^(\d+)\.(.*)"
MERGE_ACTION_SPACE = {"APPEND", "UPDATE", "ABORT"}
...
clean_line = m.group(2).strip()                              # "ACTION::APPEND"
parse_line = clean_line.split(CONFIG.llm_tab_separator)      # llm_tab_separator = "::"
if len(parse_line) < 2:
    continue
action = parse_line[0].upper().strip()                       # -> "ACTION"
if action not in MERGE_ACTION_SPACE:
    continue                                                 # <- silently dropped
```

The parser takes `parts[0]` as the action, but the model's `ACTION::APPEND` puts the literal placeholder in `parts[0]` — `"ACTION"` is not in `MERGE_ACTION_SPACE`, so the entry is dropped **even though the real action is sitting right there in `parts[1]`**.

Where the model gets `ACTION` from — `zh_merge_profile_yolo.py`, "output template" section:

```
## 输出模版
THOUGHT
---
1. ACTION{tab}...
2. ACTION{tab}...
```

versus the "output actions" section further up, which shows the correct `N. APPEND{tab}APPEND`. Three places in the same prompt describe the output format inconsistently, and the model follows the template (the literal `ACTION{tab}` placeholder) in a significant fraction of calls. The same inconsistency exists in the English prompt.

`merge_profile_yolo.py` also calls `llm_complete(...)` **without `max_tokens`**, so it inherits the default 1024 — while a batch of 30+ memos each needing a full rewritten memo easily exceeds that, producing truncated (and therefore unparseable) responses.

## Impact (observed in a ~2-month production deployment)

- Profile merges failing ~70-100% of calls in some periods; profile layer updates lag far behind the event layer
- Silent: no exception, no retry, each dropped item logs only `WARNING - No Corresponding Merge Action`
- A single merge call handles many memos, so one malformed response loses a whole batch

## Reproduction

Call the parser directly (no LLM needed):

```python
from memobase_server.prompts.utils import parse_string_into_merge_yolo_action as P

P("1. ACTION::APPEND")            # -> {}          (action "ACTION" rejected)
P("1. APPEND")                    # -> {}          (len(parts) < 2)
P("1. ACTION::UPDATE::some text") # -> {}          (same as first case)
P("1. UPDATE::some text")         # -> {1: ...}    OK
P("2. APPEND::APPEND")            # -> {2: ...}    OK
```

And a single probe against the prompt shows the model emitting `ACTION::` prefixes:

```
1. ACTION::APPEND
2. ACTION::UPDATE::<text>
```

## Proposed fix

Three small changes (a PR follows):

1. **Parser tolerance** — strip a leading `ACTION::` prefix; treat a bare `APPEND` / `ABORT` after the number as a valid action (a bare `UPDATE` cannot reconstruct the memo, so it stays dropped).
2. **Prompt consistency** — replace the literal `ACTION{tab}` placeholder in the template section with concrete examples, and drop the "just output the word `APPEND`" hint that invites bare-word output. Both zh and en.
3. **Explicit budget** — pass `max_tokens` to the merge `llm_complete` call so batches aren't truncated.

Plus a guard log when parsing yields zero actions for a non-empty input, so this class of failure becomes an alertable event instead of a silent drop.

Happy to adjust the approach if you'd prefer a different direction — the parser change alone is ~6 lines and backwards compatible.

## Notes

- Related: #114 (`perf: reduce profile merge token usage`) and #120 which introduced the YOLO merge path — this is a correctness follow-up to those.
- I searched existing issues and didn't find this reported; apologies if I missed a duplicate.
