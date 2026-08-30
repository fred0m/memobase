# -*- coding: utf-8 -*-
from typing import Optional
from fastapi import Request, Path, Query, Body
from ..controllers import full as controllers
from ..models import response as res
from ..models.response import UUID


async def upsert_user_summary(
    request: Request,
    user_id: UUID = Path(..., description="The ID of the user"),
    summary_data: res.UserSummaryUpsertData = Body(..., description="Summary data to upsert"),
) -> res.IdResponse:
    """FR-002: Upsert user summary (daily/weekly/monthly)."""
    project_id = request.state.memobase_project_id
    p = await controllers.summary.upsert_user_summary(
        user_id=str(user_id),
        project_id=project_id,
        summary_date=summary_data.summary_date,
        kind=summary_data.kind,
        content=summary_data.content,
        event_ids=summary_data.event_ids,
    )
    if not p.ok():
        return p.to_response(res.IdResponse)
    return res.IdResponse(
        data=res.IdData(id=p.data()),
        errno=res.CODE.SUCCESS,
        errmsg="",
    )


async def get_user_summaries(
    request: Request,
    user_id: UUID = Path(..., description="The ID of the user"),
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    kind: str = Query("daily", description="Summary kind: daily|weekly|monthly"),
) -> res.UserSummariesDataResponse:
    """FR-003: List user summaries in ascending chronological order."""
    project_id = request.state.memobase_project_id
    p = await controllers.summary.get_user_summaries(
        user_id=str(user_id),
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        kind=kind,
    )
    return p.to_response(res.UserSummariesDataResponse)


async def search_user_summaries(
    request: Request,
    user_id: UUID = Path(..., description="The ID of the user"),
    query: str = Query(..., description="The query to search for"),
    topk: int = Query(3, description="Number of summaries to retrieve, default is 3"),
    kind: str = Query("daily", description="Summary kind: daily|weekly|monthly"),
) -> res.UserSummariesDataResponse:
    """FR-004: Semantic search user summaries using pgvector cosine similarity."""
    project_id = request.state.memobase_project_id
    p = await controllers.summary.search_user_summaries(
        user_id=str(user_id),
        project_id=project_id,
        query=query,
        topk=topk,
        kind=kind,
    )
    return p.to_response(res.UserSummariesDataResponse)


async def rebuild_user_summary(
    request: Request,
    user_id: UUID = Path(..., description="The ID of the user"),
    date: Optional[str] = Query(None, description="Date to rebuild (YYYY-MM-DD)"),
    kind: Optional[str] = Query(None, description="Summary kind: daily|weekly|monthly"),
    style: Optional[str] = Query(None, description="Rebuild style: concat|llm"),
    body: Optional[res.RebuildSummaryRequest] = Body(None, description="Optional rebuild payload"),
) -> res.RebuildSummaryResponse:
    """FR-005: Rebuild daily summary from user events (concat|llm)."""
    project_id = request.state.memobase_project_id

    # 支持 body 或 query 参数
    target_date = (body.date if body and body.date else date) or ""
    target_kind = (body.kind if body and body.kind else kind) or "daily"
    target_style = (body.style if body and body.style else style) or "concat"

    if not target_date:
        return res.RebuildSummaryResponse(
            data=None,
            errno=res.CODE.BAD_REQUEST,
            errmsg="Missing required parameter: date",
        )

    p = await controllers.summary.rebuild_user_summary(
        user_id=str(user_id),
        project_id=project_id,
        target_date=target_date,
        kind=target_kind,
        style=target_style,
    )
    return p.to_response(res.RebuildSummaryResponse)
