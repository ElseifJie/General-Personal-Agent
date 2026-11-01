"""Agent tools for C2 long-term memory and knowledge base retrieval."""

from __future__ import annotations

from typing import Any

from general_personal_agent.gpa.context.retriever import GpaContextRetriever
from general_personal_agent.gpa.task.tools import TaskToolState


def build_context_tools(state: TaskToolState, retriever: GpaContextRetriever) -> list:
    async def search_memory(query: str) -> list[dict[str, Any]]:
        """检索当前用户的长期记忆（memories/<type> 与 peers/<peer>/memories）；query 用自然语言。"""
        return await retriever.search_memory(query, state.app_user_id, state.tenant_id, limit=5)

    async def search_knowledge(query: str, scope: str = "all") -> list[dict[str, Any]]:
        """检索知识库。scope: all / personal（个人 resources/）/ shared（共享 viking://resources）。"""
        return await retriever.search_knowledge(query, state.app_user_id, state.tenant_id, scope=scope)

    return [search_memory, search_knowledge]
