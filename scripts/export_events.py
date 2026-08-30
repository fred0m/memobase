#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出 memobase 事件 → JSON（供 memU 导入/评测使用）
只读 memobase API，不写数据。
用法: python3 export_events.py
输出: ~/hotProject/Hermes/memobase/scripts/exported_events.json
"""
import json, urllib.request

UID = "7e90d29c-fbfe-5fe1-92b5-5c90bf384026"
BASE = "http://192.168.100.246:8019/api/v1"
TOKEN = "momo_secret_for_memobase"
OUT = "/Users/mac/hotProject/Hermes/memobase/scripts/exported_events.json"

req = urllib.request.Request(
    f"{BASE}/users/event/{UID}?topk=2000&need_summary=false",
    headers={"Authorization": f"Bearer {TOKEN}"})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.loads(r.read())

events = data.get("events", data.get("data", {}).get("events", []))
print(f"拉取事件: {len(events)} 条")

# 规整成干净结构（只留评测需要的字段）
out = []
for ev in events:
    eid = ev.get("id")
    tip = (ev.get("event_data") or {}).get("event_tip") or ""
    created = ev.get("created_at", "")[:10]
    if not tip:
        continue
    out.append({
        "id": eid,
        "created_at": created,
        "event_tip": tip,
    })

out.sort(key=lambda x: x["created_at"])
print(f"有效事件: {len(out)} 条")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"已导出 → {OUT}")

# 按天分布预览
from collections import Counter
days = Counter(x["created_at"] for x in out)
print("\n按天分布（前 10 天）:")
for d, c in sorted(days.items(), reverse=True)[:10]:
    print(f"  {d}: {c} 条")
