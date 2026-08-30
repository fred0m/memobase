# -*- coding: utf-8 -*-
import re
from datetime import datetime, date, timedelta
from typing import Optional, List, Tuple
from sqlalchemy import desc, select, func
from sqlalchemy.dialects.postgresql import JSONB

from ..models.database import UserSummary, UserEvent
from ..models.response import (
    UserSummaryData,
    UserSummariesData,
    RebuildSummaryData,
    CODE,
)
from ..models.utils import Promise
from ..connectors import Session
from ..llms.embeddings import get_embedding
from ..llms import llm_complete
from ..prompts.zh_daily_summary_llm import get_prompt as get_daily_summary_llm_prompt
from ..env import TRACE_LOG, CONFIG, LOG


_USELESS_PATTERNS = {
    "无新信息",
    "暂无新信息",
    "没有新信息",
    "无新增信息",
    "无特别信息",
    "无明显信息",
    "无变化",
    "无新事件",
    "暂无",
    "无",
    "no new information",
    "no new info",
    "none",
}


def is_useless_event_line(line: str) -> bool:
    """FR-008: 概要生成清洗，丢弃无新信息类空行。"""
    if not line:
        return True
    cleaned = line.strip().lstrip("-*• 	").strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    if lowered in _USELESS_PATTERNS:
        return True
    for p in _USELESS_PATTERNS:
        if cleaned == p or cleaned.startswith(f"[{p}") or cleaned.startswith(f"【{p}"):
            return True
    return False


def _parse_date(d: str | date) -> date:
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d).strip(), "%Y-%m-%d").date()


def _extract_embedding_input(content: str, max_chars: int = 4000) -> str:
    """FR-002: embedding 输入截断：content 取「主题行（每事件首行）+ 前 4000 字符」"""
    lines = content.splitlines()
    theme_lines = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("-") or s.startswith("*") or s.startswith("【"):
            theme_lines.append(s[:100])
    theme_prefix = "\n".join(theme_lines[:30])
    body_trunc = content[:max_chars]
    if theme_prefix:
        return f"{theme_prefix}\n\n{body_trunc}"[:max_chars]
    return body_trunc


async def upsert_user_summary(
    user_id: str,
    project_id: str,
    summary_date: str | date,
    kind: str = "daily",
    content: str = "",
    event_ids: Optional[List[str]] = None,
) -> Promise[str]:
    """FR-002: 按唯一约束 (user_id, project_id, summary_date, kind) 幂等 upsert 概要。"""
    try:
        s_date = _parse_date(summary_date)
    except Exception as e:
        return Promise.reject(CODE.BAD_REQUEST, f"Invalid summary_date format: {e}")

    if kind not in ("daily", "weekly", "monthly"):
        return Promise.reject(CODE.BAD_REQUEST, f"Invalid kind: {kind}")

    if event_ids is None:
        event_ids = []
    # 确保 event_ids 为字符串列表
    event_ids = [str(eid) for eid in event_ids]

    embedding = None
    if CONFIG.enable_event_embedding and content:
        embed_input = _extract_embedding_input(content)
        try:
            embed_res = await get_embedding(
                project_id,
                [embed_input],
                phase="document",
                model=CONFIG.embedding_model,
            )
            if embed_res.ok():
                emb_data = embed_res.data()
                if emb_data is not None and len(emb_data) > 0:
                    emb = emb_data[0]
                    if hasattr(emb, "shape") and emb.shape[-1] != CONFIG.embedding_dim:
                        TRACE_LOG.error(
                            project_id,
                            user_id,
                            f"Embedding dim mismatch: expected {CONFIG.embedding_dim}, got {emb.shape[-1]}",
                        )
                    else:
                        embedding = emb
            else:
                TRACE_LOG.error(
                    project_id,
                    user_id,
                    f"Failed to get summary embedding: {embed_res.msg()}",
                )
        except Exception as e:
            TRACE_LOG.error(
                project_id,
                user_id,
                f"Exception during summary embedding calculation: {e}",
            )
            embedding = None

    with Session() as session:
        existing = (
            session.query(UserSummary)
            .filter_by(
                user_id=user_id,
                project_id=project_id,
                summary_date=s_date,
                kind=kind,
            )
            .first()
        )
        if existing is not None:
            existing.content = content
            existing.event_ids = event_ids
            if embedding is not None:
                existing.embedding = embedding
            existing.updated_at = func.now()
            session.commit()
            sid = str(existing.id)
            TRACE_LOG.info(
                project_id,
                user_id,
                f"Updated summary {sid} for date {s_date} (kind={kind}, events={len(event_ids)})",
            )
        else:
            new_summary = UserSummary(
                user_id=user_id,
                project_id=project_id,
                summary_date=s_date,
                kind=kind,
                content=content,
                event_ids=event_ids,
                embedding=embedding,
            )
            session.add(new_summary)
            session.commit()
            sid = str(new_summary.id)
            TRACE_LOG.info(
                project_id,
                user_id,
                f"Created summary {sid} for date {s_date} (kind={kind}, events={len(event_ids)})",
            )

    return Promise.resolve(sid)


async def get_user_summaries(
    user_id: str,
    project_id: str,
    start_date: Optional[str | date] = None,
    end_date: Optional[str | date] = None,
    kind: str = "daily",
) -> Promise[UserSummariesData]:
    """FR-003: 概要列表查询，按时间正序返回。"""
    with Session() as session:
        query = session.query(UserSummary).filter_by(
            user_id=user_id, project_id=project_id, kind=kind
        )
        if start_date:
            try:
                s_dt = _parse_date(start_date)
                query = query.filter(UserSummary.summary_date >= s_dt)
            except Exception as e:
                return Promise.reject(CODE.BAD_REQUEST, f"Invalid start_date: {e}")
        if end_date:
            try:
                e_dt = _parse_date(end_date)
                query = query.filter(UserSummary.summary_date <= e_dt)
            except Exception as e:
                return Promise.reject(CODE.BAD_REQUEST, f"Invalid end_date: {e}")

        summaries = query.order_by(UserSummary.summary_date.asc()).all()
        results = [
            UserSummaryData(
                id=s.id,
                user_id=s.user_id,
                project_id=s.project_id,
                summary_date=s.summary_date.strftime("%Y-%m-%d") if isinstance(s.summary_date, date) else str(s.summary_date),
                kind=s.kind,
                content=s.content,
                event_ids=[str(eid) for eid in (s.event_ids or [])],
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in summaries
        ]
    return Promise.resolve(UserSummariesData(summaries=results))


async def search_user_summaries(
    user_id: str,
    project_id: str,
    query: str,
    topk: int = 3,
    kind: str = "daily",
) -> Promise[UserSummariesData]:
    """FR-004: 概要语义检索路由，pgvector 余弦相似度排序，跳过 embedding IS NULL 行。"""
    if not CONFIG.enable_event_embedding:
        TRACE_LOG.warning(
            project_id,
            user_id,
            "Event embedding is not enabled, skip summary search",
        )
        return Promise.reject(
            CODE.NOT_IMPLEMENTED,
            "Event embedding is not enabled",
        )

    query_embeddings = await get_embedding(
        project_id, [query], phase="query", model=CONFIG.embedding_model
    )
    if not query_embeddings.ok():
        TRACE_LOG.error(
            project_id,
            user_id,
            f"Failed to get summary query embeddings: {query_embeddings.msg()}",
        )
        return query_embeddings
    query_embedding = query_embeddings.data()[0]

    stmt = (
        select(
            UserSummary,
            (1 - UserSummary.embedding.cosine_distance(query_embedding)).label("similarity"),
        )
        .where(
            UserSummary.user_id == user_id,
            UserSummary.project_id == project_id,
            UserSummary.kind == kind,
            UserSummary.embedding.isnot(None),
        )
        .order_by(desc("similarity"))
        .limit(topk)
    )

    with Session() as session:
        result = session.execute(stmt).all()
        summaries: List[UserSummaryData] = []
        for row in result:
            summary_obj: UserSummary = row[0]
            sim: float = float(row[1]) if row[1] is not None else 0.0
            summaries.append(
                UserSummaryData(
                    id=summary_obj.id,
                    user_id=summary_obj.user_id,
                    project_id=summary_obj.project_id,
                    summary_date=summary_obj.summary_date.strftime("%Y-%m-%d") if isinstance(summary_obj.summary_date, date) else str(summary_obj.summary_date),
                    kind=summary_obj.kind,
                    content=summary_obj.content,
                    event_ids=[str(eid) for eid in (summary_obj.event_ids or [])],
                    created_at=summary_obj.created_at,
                    updated_at=summary_obj.updated_at,
                    similarity=sim,
                )
            )

    return Promise.resolve(UserSummariesData(summaries=summaries))


async def rebuild_user_summary(
    user_id: str,
    project_id: str,
    target_date: str | date,
    kind: str = "daily",
    style: str = "concat",
) -> Promise[RebuildSummaryData]:
    """FR-005: 概要重算路由（concat|llm）。拉取该日全部事件，清洗无新信息行，按时间序拼接并 upsert。"""
    try:
        s_date = _parse_date(target_date)
    except Exception as e:
        return Promise.reject(CODE.BAD_REQUEST, f"Invalid date format: {e}")

    date_str = s_date.strftime("%Y-%m-%d")

    with Session() as session:
        # 查询该日全部事件 (按 func.date 过滤，兼顾时区前后一天范围匹配)
        events = (
            session.query(UserEvent)
            .filter(
                UserEvent.user_id == user_id,
                UserEvent.project_id == project_id,
                func.date(UserEvent.created_at) == s_date,
            )
            .order_by(UserEvent.created_at.asc())
            .all()
        )

        valid_lines: List[str] = []
        contributing_event_ids: List[str] = []

        for ev in events:
            ev_data = ev.event_data or {}
            tip = ev_data.get("event_tip")
            ev_has_valid_line = False
            if tip:
                for line in tip.splitlines():
                    if is_useless_event_line(line):
                        continue
                    clean_line = line.strip()
                    if not clean_line.startswith("-") and not clean_line.startswith("*"):
                        clean_line = f"- {clean_line}"
                    valid_lines.append(clean_line)
                    ev_has_valid_line = True
            
            # 同时也检查 profile_delta 补充信息（如有）
            p_deltas = ev_data.get("profile_delta") or []
            for pd in p_deltas:
                c = pd.get("content") if isinstance(pd, dict) else None
                if c and not is_useless_event_line(c):
                    clean_c = c.strip()
                    if not clean_c.startswith("-") and not clean_c.startswith("*"):
                        clean_c = f"- {clean_c}"
                    if clean_c not in valid_lines:
                        valid_lines.append(clean_c)
                        ev_has_valid_line = True

            if ev_has_valid_line:
                contributing_event_ids.append(str(ev.id))

    # FR-006: 当日无有效事件行时不写入
    if not valid_lines or not contributing_event_ids:
        TRACE_LOG.info(
            project_id,
            user_id,
            f"No valid event lines found for date {date_str}, skipping summary write",
        )
        return Promise.resolve(
            RebuildSummaryData(
                event_count=0,
                summary_id=None,
                summary_date=date_str,
                kind=kind,
            )
        )

    concat_content = "\n".join(valid_lines)
    final_content = concat_content

    if style == "llm":
        # style=llm 压缩版 (FR-005)
        try:
            prompt = get_daily_summary_llm_prompt(date_str, concat_content)
            llm_res = await llm_complete(
                project_id,
                prompt,
                model=CONFIG.best_llm_model,
                max_tokens=1500,
            )
            if llm_res.ok():
                final_content = str(llm_res.data()).strip()
            else:
                TRACE_LOG.warning(
                    project_id,
                    user_id,
                    f"LLM daily summary failed, fallback to concat: {llm_res.msg()}",
                )
                final_content = concat_content
        except Exception as e:
            TRACE_LOG.error(
                project_id,
                user_id,
                f"Exception in LLM daily summary: {e}, fallback to concat",
            )
            final_content = concat_content

    p_upsert = await upsert_user_summary(
        user_id=user_id,
        project_id=project_id,
        summary_date=s_date,
        kind=kind,
        content=final_content,
        event_ids=contributing_event_ids,
    )
    if not p_upsert.ok():
        return Promise.reject(p_upsert.code(), p_upsert.msg())

    sid = p_upsert.data()
    return Promise.resolve(
        RebuildSummaryData(
            event_count=len(contributing_event_ids),
            summary_id=sid,
            summary_date=date_str,
            kind=kind,
        )
    )
