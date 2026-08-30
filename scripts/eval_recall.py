#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memobase hybrid v3 离线验收：4 配置对拍（v2 基线 / +A 时间 / +B 实体 / +A+B）
图图方案 §5 的验收工具。只读 API，不写数据。
用法: python3 eval_recall.py
"""
import os, sys, json, time, urllib.request
from datetime import datetime

os.environ.setdefault("MEMOBASE_BASE_URL", "http://192.168.100.246:8019/api/v1")
os.environ.setdefault("MEMOBASE_API_KEY", "momo_secret_for_memobase")
sys.path.insert(0, "/Users/mac/.hermes/plugins")

from memobase.hybrid_retriever import (
    EventStore, rrf_fusion_scored, parse_time_window, temporal_factor,
    extract_entities, _rrf_scores,
)

UID = "7e90d29c-fbfe-5fe1-92b5-5c90bf384026"
BASE = "http://192.168.100.246:8019/api/v1"

# ---------- 1. 拉事件 ----------
def fetch_events():
    req = urllib.request.Request(
        f"{BASE}/users/event/{UID}?topk=1000&need_summary=false",
        headers={"Authorization": "Bearer momo_secret_for_memobase"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data.get("events", data.get("data", {}).get("events", []))

events = fetch_events()
texts, times = {}, {}
for ev in events:
    eid = ev.get("id")
    tip = (ev.get("event_data") or {}).get("event_tip") or ""
    texts[eid] = tip
    ts = ev.get("created_at")
    if ts:
        try:
            times[eid] = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
print(f"事件: {len(texts)} 条, 时间: {len(times)} 条")

# ---------- 2. 实体倒排 ----------
entity_index = {}
for eid, txt in texts.items():
    for ent in extract_entities(txt):
        entity_index.setdefault(ent, []).append(eid)

def entity_leg(query, topk=20):
    ents = extract_entities(query)
    if not ents:
        return []
    scores = {}
    for ent in ents:
        df = len(entity_index.get(ent, []))
        if df == 0:
            continue
        w = 1.0 / df
        for eid in entity_index[ent]:
            scores[eid] = scores.get(eid, 0.0) + w
    return [eid for eid, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:topk]]

# ---------- 3. BM25 腿（复用 hybrid_retriever 的 BM25Index）----------
from memobase.hybrid_retriever import BM25Index
bm25 = BM25Index()
bm25.build(list(texts.values()))
bm25_ids_by_query = {}

def bm25_leg(query, topk=20):
    if query not in bm25_ids_by_query:
        scored = bm25.score(query)  # List[Tuple[int, float]] — doc index + score
        doc_ids = list(texts.keys())
        bm25_ids_by_query[query] = [doc_ids[i] for i, _ in scored[:topk]]
    return bm25_ids_by_query[query]

# ---------- 4. 向量腿（真实 API 语义搜索）----------
def vector_leg(query, topk=20):
    import urllib.parse
    q = urllib.parse.quote(json.dumps({"query": query}, ensure_ascii=False))
    req = urllib.request.Request(
        f"{BASE}/users/event/search/{UID}?query={q}&topk={topk}",
        headers={"Authorization": "Bearer momo_secret_for_memobase"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        hits = data.get("events", data.get("data", {}).get("events", []))
        return [(h.get("id"), h.get("similarity")) for h in hits if h.get("id")]
    except Exception as e:
        print(f"  [vector leg 失败: {e}]")
        return []

# ---------- 5. 4 配置融合 ----------
def fuse(query, use_temporal, use_entity, topk=10):
    vec = vector_leg(query, topk=20)
    vec_ids = [eid for eid, _ in vec]
    vec_sims = [sim for _, sim in vec]
    bm25_ids = bm25_leg(query, topk=20)
    ent_ids = entity_leg(query, topk=20) if use_entity else []

    legs = [vec_ids, bm25_ids] + ([ent_ids] if ent_ids else [])
    fused_scored = rrf_fusion_scored(legs, k=60)

    if use_temporal:
        window = parse_time_window(query)
        if window:
            now = time.time()
            boosted = []
            for eid, s in fused_scored:
                t = times.get(eid)
                age = (now - t) / 86400.0 if t else None
                f = temporal_factor(age, window) if age is not None else 1.0
                boosted.append((eid, s * f))
            boosted.sort(key=lambda kv: kv[1], reverse=True)
            fused_scored = boosted
    return [eid for eid, _ in fused_scored[:topk]]

# ---------- 6. 标注集（40 查询，覆盖时间词/实体/短查询/模糊长查询）----------
# 每个查询: (query, [应命中的事件 id 前缀], 类型)
QUERIES = [
    # 时间词类（应命中近期事件）
    ("最近配的 rerank 是什么模型", ["8c8bbb4c", "afcf78fb"], "temporal"),
    ("上周说的混合检索方案", ["8c8bbb4c"], "temporal"),
    ("最近聊的 mem0 评估", ["d54b854d"], "temporal"),
    ("今天创建的 bot 叫什么", ["da7010d3", "c9a69132", "f48cf71c"], "temporal"),
    ("最近关于 async 池的讨论", ["9e45addf", "0edece93", "3cbe9324"], "temporal"),
    ("最近要拍板的事", ["c5664cdb"], "temporal"),
    ("这几天关于知识库的讨论", ["9b606429", "5ee8955c", "4eedb0b7"], "temporal"),
    ("最近关于账单的讨论", ["96eaa088", "c97f0ca1"], "temporal"),
    ("最近关于 Gemini 订阅的讨论", ["37b746fa", "1b65fcb6"], "temporal"),
    ("最近关于模型切换的讨论", ["4a9e29aa", "66aef396", "3da61de7"], "temporal"),
    # 实体类（应命中含实体的精确事件）
    ("hybrid_retriever.py 里 RRF 的 k 是多少", ["8c8bbb4c"], "entity"),
    ("9router 的 memobase-use 映射", ["8c8bbb4c"], "entity"),
    ("Qwen3-Reranker-4B 选型", ["8c8bbb4c", "afcf78fb"], "entity"),
    ("GOALS.md 目标推进", ["7f95c4f7"], "entity"),
    ("firefly-bookkeeping 账单样本", ["96eaa088"], "entity"),
    ("SOUL.md 关系升级", ["4619b14c", "9377afc5", "97e47aae"], "entity"),
    ("宋玉 大额存单", ["ab758c0e", "5dd9caec"], "entity"),
    ("deepseek-v4-flash 主力模型", ["3da61de7", "4a9e29aa", "66aef396"], "entity"),
    ("图图 Atlas 架构设计", ["da7010d3", "c9a69132", "f48cf71c"], "entity"),
    ("chromebook 算力调度", ["9e45addf", "0edece93", "3cbe9324"], "entity"),
    # 短查询类
    ("生日", ["20ff07cc"], "short"),
    ("名字", ["930b0fdd"], "short"),
    ("模型", ["3da61de7", "4a9e29aa"], "short"),
    ("咖啡", ["930b0fdd"], "short"),
    ("妈妈", ["ab758c0e", "5dd9caec"], "short"),
    # 模糊长查询类
    ("总结一下之前的讨论", [], "vague"),
    ("我们之前聊过什么技术方案", ["8c8bbb4c", "d54b854d"], "vague"),
    ("用户和助手的关系", ["4619b14c", "9377afc5", "97e47aae", "6e3869b2"], "vague"),
    ("家庭财务相关", ["ab758c0e", "5dd9caec", "37b746fa", "1b65fcb6"], "vague"),
    ("关于记忆系统的讨论", ["d54b854d", "8c8bbb4c"], "vague"),
    ("关于 bot 的讨论", ["da7010d3", "c9a69132", "f48cf71c"], "vague"),
    ("关于模型和 API 的讨论", ["3da61de7", "4a9e29aa", "66aef396", "95186e99", "713c277a"], "vague"),
    ("关于知识管理的讨论", ["9b606429", "5ee8955c", "4eedb0b7"], "vague"),
    ("关于财务分析的讨论", ["ab758c0e", "5dd9caec", "37b746fa", "1b65fcb6"], "vague"),
    ("关于家庭成员的讨论", ["ab758c0e", "5dd9caec", "930b0fdd"], "vague"),
    # 混合类（时间+实体）
    ("最近关于 rerank 模型的讨论", ["8c8bbb4c", "afcf78fb"], "mixed"),
    ("最近关于图图的讨论", ["da7010d3", "c9a69132", "f48cf71c"], "mixed"),
    ("最近关于 async 调度器的讨论", ["9e45addf", "0edece93", "3cbe9324", "c5664cdb"], "mixed"),
    ("最近关于知识库的讨论", ["9b606429", "5ee8955c", "4eedb0b7"], "mixed"),
    ("最近关于 Gemini 的讨论", ["37b746fa", "1b65fcb6"], "mixed"),
    ("最近关于账单样本的讨论", ["96eaa088", "c97f0ca1"], "mixed"),
    ("最近关于模型切换的讨论", ["4a9e29aa", "66aef396", "3da61de7"], "mixed"),
    ("最近关于 SOUL 的讨论", ["4619b14c", "9377afc5", "97e47aae"], "mixed"),
]

# ---------- 7. 指标 ----------
def mrr_at10(ranked, gold):
    for i, eid in enumerate(ranked[:10]):
        if any(eid.startswith(g) for g in gold):
            return 1.0 / (i + 1)
    return 0.0

def recall_at10(ranked, gold):
    if not gold:
        return 1.0  # 无标注查询视为不惩罚
    hit = sum(1 for g in gold if any(eid.startswith(g) for eid in ranked[:10]))
    return hit / len(gold)

def run_config(use_temporal, use_entity, label):
    mrr, rec = [], []
    by_type = {}
    for query, gold, qtype in QUERIES:
        ranked = fuse(query, use_temporal, use_entity)
        m = mrr_at10(ranked, gold)
        r = recall_at10(ranked, gold)
        mrr.append(m)
        rec.append(r)
        by_type.setdefault(qtype, []).append(m)
    avg_mrr = sum(mrr) / len(mrr)
    avg_rec = sum(rec) / len(rec)
    type_str = "  ".join(f"{t}={sum(v)/len(v):.3f}" for t, v in sorted(by_type.items()))
    print(f"{label:12s} MRR@10={avg_mrr:.4f}  Recall@10={avg_rec:.4f}  [{type_str}]")
    return avg_mrr, avg_rec, by_type

print("\n===== 4 配置对拍 =====")
r1 = run_config(False, False, "v2 基线")
r2 = run_config(True, False, "+A 时间")
r3 = run_config(False, True, "+B 实体")
r4 = run_config(True, True, "+A+B")

print("\n===== 结论 =====")
print(f"v2 基线:  MRR={r1[0]:.4f} Recall={r1[1]:.4f}")
print(f"+A 时间:  MRR={r2[0]:.4f} Recall={r2[1]:.4f}  (ΔMRR={r2[0]-r1[0]:+.4f})")
print(f"+B 实体:  MRR={r3[0]:.4f} Recall={r3[1]:.4f}  (ΔMRR={r3[0]-r1[0]:+.4f})")
print(f"+A+B:     MRR={r4[0]:.4f} Recall={r4[1]:.4f}  (ΔMRR={r4[0]-r1[0]:+.4f})")
