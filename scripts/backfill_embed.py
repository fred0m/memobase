#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填 user_event_gists 存量 embedding（NULL → Qwen3-Embedding-4B @1536）"""
import json, subprocess, urllib.request, urllib.error, time, sys

PG = ["docker", "exec", "shared-postgres", "psql", "-U", "postgres", "-d", "memobase", "-t", "-A"]

def psql(sql):
    r = subprocess.run(PG + ["-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr}")
    return r.stdout

def psql_stdin(sql):
    r = subprocess.run(["docker", "exec", "-i", "shared-postgres", "psql", "-U", "postgres", "-d", "memobase"],
                       input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"psql stdin failed: {r.stderr[-500:]}")
    return r.stdout

# 1. 导出待回填
out = psql("SELECT json_build_object('id', id::text, 'c', gist_data->>'content') FROM user_event_gists WHERE embedding IS NULL")
rows = [json.loads(l) for l in out.strip().splitlines() if l.strip()]
print(f"待回填: {len(rows)} 条", flush=True)

# 2. embedding key
key = open("/home/momo/memobase/config.yaml").read().split('embedding_api_key: "')[1].split('"')[0]

def embed_batch(texts):
    body = json.dumps({"model": "Qwen/Qwen3-Embedding-4B", "input": texts, "dimensions": 1536}).encode()
    req = urllib.request.Request("https://api.siliconflow.cn/v1/embeddings", data=body,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=60)
    return [d["embedding"] for d in json.loads(r.read())["data"]]

# 3. 分批回填
BATCH = 16
done = 0
for i in range(0, len(rows), BATCH):
    batch = rows[i:i + BATCH]
    texts = [(r["c"] or "")[:4000] for r in batch]
    for attempt in range(3):
        try:
            vecs = embed_batch(texts)
            break
        except Exception as e:
            print(f"  batch {i} attempt {attempt+1} failed: {e}", flush=True)
            time.sleep(3)
    else:
        print(f"  batch {i} FAILED after 3 attempts, skip", flush=True)
        continue
    sql_parts = []
    for r, vec in zip(batch, vecs):
        vec_str = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
        sql_parts.append(f"UPDATE user_event_gists SET embedding='{vec_str}'::vector, updated_at=now() WHERE id='{r['id']}' AND project_id='__root__';")
    psql_stdin("\n".join(sql_parts))
    done += len(batch)
    print(f"  回填 {done}/{len(rows)}", flush=True)

print(f"完成，成功 {done} 条")