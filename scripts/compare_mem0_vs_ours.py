#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接对比实验：mem0 现状（to_tsvector('simple')）vs 我们的 CJK unigram+bigram BM25
同一中文语料（110 条真实事件），同一标注查询集，对比召回质量。

mem0 侧模拟：pgvector keyword_search 用 to_tsvector('simple', text_lemmatized)，
'simple' 配置按空白/标点切分，中文连续字符（无空格）= 1 个 token。
查询同样 plainto_tsquery('simple', query) → 中文整句 1 个 token。
→ 只有文档 token 与查询 token 完全相等才匹配。

我们侧：tokenize()（CJK unigram+bigram + 拉丁词）+ Okapi BM25。

用法: python3 compare_mem0_vs_ours.py
"""
import os, sys, json, re, urllib.request
from datetime import datetime

sys.path.insert(0, "/Users/mac/.hermes/plugins")
from memobase.hybrid_retriever import tokenize, BM25Index

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
texts = {}
for ev in events:
    eid = ev.get("id")
    tip = (ev.get("event_data") or {}).get("event_tip") or ""
    texts[eid] = tip
doc_ids = list(texts.keys())
print(f"事件: {len(texts)} 条")

# ---------- 2. mem0 侧：'simple' 分词模拟 ----------
# PostgreSQL to_tsvector('simple')：按非字母数字切分，CJK 字符算字母（word）
# 中文无空格 → 连续中文 = 1 个 token
_SIMPLE_RE = re.compile(r"[\w\u4e00-\u9fff\u3400-\u4dbf]+")

def simple_tokenize(text: str) -> list:
    return _SIMPLE_RE.findall(text.lower())

# 预计算每篇文档的 simple tokens
doc_simple = {eid: simple_tokenize(txt) for eid, txt in texts.items()}

def mem0_simple_search(query: str, topk: int = 10) -> list:
    """模拟 pgvector keyword_search：查询 token 与文档 token 完全相等才匹配。
    打分 = 匹配 token 数（近似 ts_rank_cd 的简化）。"""
    q_tokens = simple_tokenize(query)
    if not q_tokens:
        return []
    scored = []
    for eid, doc_toks in doc_simple.items():
        # 查询 token 集合与文档 token 集合的交集计数
        qset = set(q_tokens)
        match = sum(1 for t in doc_toks if t in qset)
        if match > 0:
            scored.append((eid, match))
    scored.sort(key=lambda kv: -kv[1])
    return [eid for eid, _ in scored[:topk]]

# ---------- 3. 我们侧：CJK unigram+bigram BM25 ----------
bm25 = BM25Index()
bm25.build(list(texts.values()))

def ours_bm25_search(query: str, topk: int = 10) -> list:
    """优化版：query_mode=True（CJK bigram-only）+ latin idf boost"""
    scored = bm25.score(query)
    return [doc_ids[i] for i, _ in scored[:topk]]

def ours_old_search(query: str, topk: int = 10) -> list:
    """原版：query_mode=False（unigram+bigram）+ 无 latin boost（手动复现）"""
    import math
    q_terms = set(tokenize(query, query_mode=False))
    if not q_terms:
        return []
    n = len(doc_ids)
    scores = {}
    for term in q_terms:
        posting = bm25._postings.get(term)
        if not posting:
            continue
        df = bm25._df[term]
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for di, tf in posting:
            dl = bm25._doc_len[di]
            denom = tf + bm25.K1 * (1.0 - bm25.B + bm25.B * dl / bm25._avgdl) if bm25._avgdl else 1.0
            scores[di] = scores.get(di, 0.0) + idf * (tf * (bm25.K1 + 1.0)) / denom
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [doc_ids[i] for i, _ in ranked[:topk]]

# ---------- 4. 标注查询集（中文为主，覆盖时间词/实体/短查询/模糊）----------
QUERIES = [
    # (query, [应命中事件 id 前缀], 类型)
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
    ("生日", ["20ff07cc"], "short"),
    ("名字", ["930b0fdd"], "short"),
    ("模型", ["3da61de7", "4a9e29aa"], "short"),
    ("咖啡", ["930b0fdd"], "short"),
    ("妈妈", ["ab758c0e", "5dd9caec"], "short"),
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
    ("最近关于 rerank 模型的讨论", ["8c8bbb4c", "afcf78fb"], "mixed"),
    ("最近关于图图的讨论", ["da7010d3", "c9a69132", "f48cf71c"], "mixed"),
    ("最近关于 async 调度器的讨论", ["9e45addf", "0edece93", "3cbe9324", "c5664cdb"], "mixed"),
    ("最近关于知识库的讨论", ["9b606429", "5ee8955c", "4eedb0b7"], "mixed"),
    ("最近关于 Gemini 的讨论", ["37b746fa", "1b65fcb6"], "mixed"),
    ("最近关于账单样本的讨论", ["96eaa088", "c97f0ca1"], "mixed"),
    ("最近关于模型切换的讨论", ["4a9e29aa", "66aef396", "3da61de7"], "mixed"),
    ("最近关于 SOUL 的讨论", ["4619b14c", "9377afc5", "97e47aae"], "mixed"),
]

# ---------- 5. 指标 ----------
def mrr_at10(ranked, gold):
    for i, eid in enumerate(ranked[:10]):
        if any(eid.startswith(g) for g in gold):
            return 1.0 / (i + 1)
    return 0.0

def recall_at10(ranked, gold):
    if not gold:
        return 1.0
    hit = sum(1 for g in gold if any(eid.startswith(g) for eid in ranked[:10]))
    return hit / len(gold)

# ---------- 6. 跑对比 ----------
print("\n===== 逐查询对比（top-10 命中）=====")
print(f"{'查询':<28s} {'mem0(simple)':<14s} {'ours旧版':<14s} {'ours优化':<14s} 类型")
mem0_mrr, mem0_rec, old_mrr, old_rec, ours_mrr, ours_rec = [], [], [], [], [], []
mem0_hit_count = old_hit_count = ours_hit_count = 0
for query, gold, qtype in QUERIES:
    m_ranked = mem0_simple_search(query)
    o_old = ours_old_search(query)
    o_ranked = ours_bm25_search(query)
    m_mrr = mrr_at10(m_ranked, gold)
    oo_mrr = mrr_at10(o_old, gold)
    o_mrr = mrr_at10(o_ranked, gold)
    m_rec = recall_at10(m_ranked, gold)
    oo_rec = recall_at10(o_old, gold)
    o_rec = recall_at10(o_ranked, gold)
    mem0_mrr.append(m_mrr); mem0_rec.append(m_rec)
    old_mrr.append(oo_mrr); old_rec.append(oo_rec)
    ours_mrr.append(o_mrr); ours_rec.append(o_rec)
    if m_rec > 0: mem0_hit_count += 1
    if oo_rec > 0: old_hit_count += 1
    if o_rec > 0: ours_hit_count += 1
    # 只打印有差异的
    if m_rec != o_rec or m_mrr != o_mrr or oo_mrr != o_mrr:
        print(f"{query[:24]:<28s} MRR={m_mrr:.2f}/R={m_rec:.2f}  MRR={oo_mrr:.2f}/R={oo_rec:.2f}  MRR={o_mrr:.2f}/R={o_rec:.2f}  {qtype}")

print("\n===== 汇总 =====")
n = len(QUERIES)
print(f"{'指标':<20s} {'mem0(simple)':<14s} {'ours旧版':<14s} {'ours优化':<14s}")
print(f"{'MRR@10':<20s} {sum(mem0_mrr)/n:.4f}        {sum(old_mrr)/n:.4f}        {sum(ours_mrr)/n:.4f}")
print(f"{'Recall@10':<20s} {sum(mem0_rec)/n:.4f}        {sum(old_rec)/n:.4f}        {sum(ours_rec)/n:.4f}")
print(f"{'有命中查询数':<20s} {mem0_hit_count}/{n}        {old_hit_count}/{n}        {ours_hit_count}/{n}")

# 按类型分
print("\n===== 按类型（MRR@10）=====")
for qtype in ["temporal", "entity", "short", "vague", "mixed"]:
    idx = [i for i, q in enumerate(QUERIES) if q[2] == qtype]
    if not idx:
        continue
    m_m = sum(mem0_mrr[i] for i in idx) / len(idx)
    oo_m = sum(old_mrr[i] for i in idx) / len(idx)
    o_m = sum(ours_mrr[i] for i in idx) / len(idx)
    m_r = sum(mem0_rec[i] for i in idx) / len(idx)
    oo_r = sum(old_rec[i] for i in idx) / len(idx)
    o_r = sum(ours_rec[i] for i in idx) / len(idx)
    print(f"{qtype:<10s} mem0: MRR={m_m:.3f} R={m_r:.3f} | 旧版: MRR={oo_m:.3f} R={oo_r:.3f} | 优化: MRR={o_m:.3f} R={o_r:.3f}")

# ---------- 7. 具体例子：展示 simple 分词 vs 我们的分词 ----------
print("\n===== 分词示例 =====")
sample = "我们最近配置的 rerank 精排是什么模型"
print(f"原文: {sample}")
print(f"mem0 simple: {simple_tokenize(sample)}")
print(f"ours CJK:   {tokenize(sample)}")
