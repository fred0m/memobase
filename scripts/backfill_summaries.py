#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FR-020: 历史数据回填日概要脚本 (scripts/backfill_summaries.py)

实现方式选择理由（REST API 方式）：
1. 解耦部署环境：开发者机、CI 或远程维护节点均可通过 REST 触发，无需在本地配置 PostgreSQL / pgvector / 依赖库环境；
2. 保证生产链路一致性：走服务端相同校验、清洗、embedding 与事务 upsert 逻辑，避免本地逻辑偏离；
3. 幂等安全：服务端唯一约束 (user_id, project_id, summary_date, kind) 保证重跑覆盖而不重写/冗余。

功能：
- 按日扫描存量 user_events
- 对每个有有效事件的日期调用 POST /api/v1/users/summary/{user_id}/rebuild
- 幂等重跑支持
- 统计输出：处理日期数、事件总数、概要生成数、耗时
"""

import os
import sys
import time
import json
import argparse
import urllib.request
import urllib.error
from collections import defaultdict

DEFAULT_BASE_URL = os.environ.get("MEMOBASE_URL", "http://192.168.100.246:8019/api/v1").rstrip("/")
DEFAULT_USER_ID = os.environ.get("MEMOBASE_USER_ID", "7e90d29c-fbfe-5fe1-92b5-5c90bf384026")
DEFAULT_TOKEN = os.environ.get("MEMOBASE_TOKEN", "momo_secret_for_memobase")


def make_request(url: str, token: str, method: str = "GET", data: dict | None = None, timeout: int = 60) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    req_body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=req_body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
        return json.loads(content)


def fetch_all_events(base_url: str, user_id: str, token: str) -> list[dict]:
    url = f"{base_url}/users/event/{user_id}?topk=2000&need_summary=false"
    resp = make_request(url, token, method="GET")
    events = resp.get("events", resp.get("data", {}).get("events", [])) if isinstance(resp, dict) else []
    return events


def backfill_summaries(base_url: str, user_id: str, token: str, start_date: str | None = None, end_date: str | None = None, style: str = "concat"):
    print(f"=== 开始回填日概要 ===")
    print(f"Server: {base_url}")
    print(f"User ID: {user_id}")
    print(f"Style: {style}")

    t0 = time.time()
    events = fetch_all_events(base_url, user_id, token)
    print(f"拉取存量事件: {len(events)} 条")

    # 按日期分组
    date_to_events = defaultdict(list)
    for ev in events:
        created_str = ev.get("created_at") or ""
        if not created_str:
            continue
        d_str = created_str[:10]  # YYYY-MM-DD
        if start_date and d_str < start_date:
            continue
        if end_date and d_str > end_date:
            continue
        date_to_events[d_str].append(ev)

    sorted_dates = sorted(date_to_events.keys())
    print(f"涉及日期数: {len(sorted_dates)} 天 ({sorted_dates[0] if sorted_dates else 'N/A'} ~ {sorted_dates[-1] if sorted_dates else 'N/A'})")

    total_events_counted = 0
    success_dates = 0
    skipped_dates = 0
    failed_dates = 0

    for idx, d_str in enumerate(sorted_dates, 1):
        day_events = date_to_events[d_str]
        total_events_counted += len(day_events)
        rebuild_url = f"{base_url}/users/summary/{user_id}/rebuild"
        payload = {
            "date": d_str,
            "kind": "daily",
            "style": style,
        }
        try:
            res = make_request(rebuild_url, token, method="POST", data=payload, timeout=60)
            data = res.get("data") or {}
            event_count = data.get("event_count", 0)
            summary_id = data.get("summary_id")
            if summary_id:
                success_dates += 1
                print(f"[{idx}/{len(sorted_dates)}] {d_str}: 成功生成概要 (id={summary_id}, 纳入事件={event_count}/{len(day_events)})")
            else:
                skipped_dates += 1
                print(f"[{idx}/{len(sorted_dates)}] {d_str}: 无有效事件行，跳过写入 (纳入事件=0/{len(day_events)})")
        except Exception as e:
            failed_dates += 1
            print(f"[{idx}/{len(sorted_dates)}] {d_str}: 失败: {e}", file=sys.stderr)

    elapsed = time.time() - t0
    print(f"\n=== 回填完成 ===")
    print(f"耗时: {elapsed:.2f}s")
    print(f"总日期数: {len(sorted_dates)}")
    print(f"成功生成概要日期数: {success_dates}")
    print(f"跳过日期数: {skipped_dates}")
    print(f"失败日期数: {failed_dates}")
    print(f"扫描事件总数: {total_events_counted}")


def main():
    parser = argparse.ArgumentParser(description="回填存量事件日概要 (FR-020)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Memobase API URL (default: %(default)s)")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="User ID (default: %(default)s)")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Auth token (default: %(default)s)")
    parser.add_argument("--start-date", default=None, help="Start date filter YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="End date filter YYYY-MM-DD")
    parser.add_argument("--style", default="concat", choices=["concat", "llm"], help="Summary style (default: concat)")

    args = parser.parse_args()
    backfill_summaries(
        base_url=args.base_url,
        user_id=args.user_id,
        token=args.token,
        start_date=args.start_date,
        end_date=args.end_date,
        style=args.style,
    )


if __name__ == "__main__":
    main()
