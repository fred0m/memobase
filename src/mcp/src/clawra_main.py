"""
克拉拉专属 Memobase MCP — 独立记忆空间（user_id = string_to_uuid("clawra")）。

与 Hermes 版（DEFAULT_USER_ID = string_to_uuid("user")）完全隔离：
- 克拉拉存/查的记忆只属于她自己
- 沫沫的画像（Hermes 维护）不会被克拉拉看到或污染

用法（streamable-http）：
    TRANSPORT=streamable-http PORT=8051 MEMOBASE_API_KEY=... MEMOBASE_BASE_URL=... \
        python clawra_main.py

供 OpenClaw（M4）通过 mcp add 远程连接：
    openclaw mcp add memobase-clawra --transport streamable-http \
        --url http://192.168.100.166:8051/mcp
"""

from mcp.server.fastmcp import FastMCP, Context
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from dotenv import load_dotenv
from memobase import AsyncMemoBaseClient, ChatBlob
from memobase.utils import string_to_uuid
import asyncio
import json
import os

from utils import get_memobase_client

load_dotenv()

# 克拉拉专属用户 ID（与 Hermes 的 "user" 完全隔离）
DEFAULT_USER_ID = string_to_uuid("clawra")


@dataclass
class MemobaseContext:
    """Context for the Memobase MCP server."""

    memobase_client: AsyncMemoBaseClient


@asynccontextmanager
async def memobase_lifespan(server: FastMCP) -> AsyncIterator[MemobaseContext]:
    memobase_client = get_memobase_client()
    assert await memobase_client.ping(), "Failed to connect to Memobase"
    print("Clawra Memobase client connected")
    try:
        yield MemobaseContext(memobase_client=memobase_client)
    finally:
        pass


mcp = FastMCP(
    "memobase-clawra",
    lifespan=memobase_lifespan,
    host=os.getenv("HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", 8051)),
)


@mcp.tool()
async def save_memory(ctx: Context, text: str) -> str:
    """Save information to your long-term memory.

    This tool is designed to store any type of information that might be useful in the future.
    The content will be processed and indexed for later retrieval through semantic search.

    Args:
        ctx: The MCP server provided context which includes the Memobase client
        text: The content to store in memory, including any relevant details and context
    """
    try:
        memobase_client: AsyncMemoBaseClient = (
            ctx.request_context.lifespan_context.memobase_client
        )
        messages = [{"role": "user", "content": text}]
        u = await memobase_client.get_or_create_user(DEFAULT_USER_ID)
        await u.insert(ChatBlob(messages=messages))
        await u.flush()
        print(f"[clawra] saved memory blob")
        return f"Successfully saved memory: {text[:100]}..."
    except Exception as e:
        return f"Error saving memory: {str(e)}"


@mcp.tool()
async def get_user_profiles(ctx: Context) -> str:
    """Get full user profiles.

    Call this tool when user asks for a summary of complete image of itself.
    """
    try:
        memobase_client: AsyncMemoBaseClient = (
            ctx.request_context.lifespan_context.memobase_client
        )
        u = await memobase_client.get_or_create_user(DEFAULT_USER_ID)
        ps = await u.profile()
        return json.dumps(ps, ensure_ascii=False, default=str)
    except Exception as e:
        return f"Error getting profiles: {str(e)}"


@mcp.tool()
async def search_memories(
    ctx: Context,
    query: str,
    max_length: int = 1000,
) -> str:
    """Search user memories

    Call this tool when user ask for recall some personal information.

    Args:
        ctx: The MCP server provided context which includes the Memobase client
        query: Search query string describing what you're looking for. Can be natural language.
        max_length: Maximum content length of the returned context.
    """
    try:
        memobase_client: AsyncMemoBaseClient = (
            ctx.request_context.lifespan_context.memobase_client
        )
        u = await memobase_client.get_or_create_user(DEFAULT_USER_ID)
        ps = await u.context(
            chats=[{"role": "user", "content": query}], max_token_size=max_length
        )
        return ps
    except Exception as e:
        return f"Error searching memories: {str(e)}"


def main():
    transport = os.getenv("TRANSPORT", "streamable-http")
    if transport == "streamable-http":
        # mcp.run() 是同步方法（内部自己 anyio.run），不能在 asyncio 循环里调用
        mcp.run(transport="streamable-http")
    elif transport == "sse":
        asyncio.run(mcp.run_sse_async())
    else:
        asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
