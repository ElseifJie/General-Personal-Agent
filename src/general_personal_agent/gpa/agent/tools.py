"""Agent tools for live web information (search provider backed).

The Personal Agent previously had no web tool at all, so any realtime question
(weather, news, date, prices) ended in "I have no internet access".  The search
providers already live in ``clients.search``; this module is the thin ADK tool
surface that exposes them to the model.
"""

from __future__ import annotations

from typing import Any

from general_personal_agent.clients.search import SearchService
from general_personal_agent.gpa.errors import GpaError
from general_personal_agent.gpa.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_SUMMARY_CHARS = 1500
MAX_SOURCES = 5


def build_search_tools(search: SearchService, *, max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS) -> list:
    async def web_search(query: str) -> dict[str, Any]:
        """联网搜索最新信息：天气、新闻、热搜、汇率、股价、比分、发布会等。

        query 用自然语言（用户当前语言）。返回摘要与来源；实时信息必须以此为准。
        """
        clean = (query or "").strip()
        if not clean:
            return {"error": "empty_query", "message": "query 不能为空"}

        try:
            result = await search.search(clean)
        except Exception as exc:  # noqa: BLE001 - tool errors must reach the model, not crash the turn
            logger.warning("web_search failed (%s): %s", clean, exc)
            return {
                "error": "search_failed",
                "query": clean,
                "reason": "exception",
                "retryable": True,
                "detail": f"{type(exc).__name__}: {exc}"[:240],
                "message": "联网搜索暂时不可用，请稍后再试。",
            }

        if result.get("error"):
            detail = str(result["error"])
            reason = result.get("kind") or "provider"
            logger.warning("web_search provider error (%s, %s): %s", clean, reason, detail[:500])
            return {
                "error": "search_failed",
                "query": clean,
                "reason": reason,
                # 凭据失效不是模型重试能解决的，告诉它就不要再连打两次
                "retryable": reason != "auth",
                "providers": [
                    {
                        "name": a.get("provider", ""),
                        "kind": a.get("kind", ""),
                        "error": a.get("error", "")[:160],
                    }
                    for a in (result.get("attempts") or [])
                ][:6],
                "detail": detail[:240],
                "message": "联网搜索服务返回错误，请稍后再试。",
            }

        summary = (result.get("summary") or "").strip()
        if len(summary) > max_summary_chars:
            summary = summary[:max_summary_chars].rstrip() + "…"
        sources = [s for s in (result.get("sources") or []) if s.get("url")][:MAX_SOURCES]
        return {
            "query": result.get("query") or clean,
            "provider": result.get("provider", ""),
            "summary": summary or "（搜索没有返回摘要）",
            "sources": sources,
            "instruction": (
                "依据 summary 回答，不要回答“无法联网”；"
                "引用外部信息时附上 sources 中的标题或链接；摘要未覆盖的内容不要编造。"
            ),
        }

    return [web_search]


def build_attachment_tools(state, files) -> list:
    """Expose only the files attached to the current turn (least privilege)."""

    async def list_attachments() -> list[dict[str, Any]]:
        """列出本轮用户附带的文件及其解析状态。"""
        out: list[dict[str, Any]] = []
        for file_id in list(getattr(state, "attachment_ids", []) or []):
            file = await files.store.get_file(file_id)
            if file is None:
                continue
            out.append({
                "file_id": file_id,
                "name": file["name"],
                "mime": file.get("mime"),
                "bytes": file.get("bytes"),
                "text_status": file.get("text_status"),
                "text_chars": file.get("text_chars") or 0,
                "truncated": bool(file.get("text_truncated")),
            })
        return out

    async def read_attachment(file_id: str, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        """分页读取本轮附件正文；offset 为字符偏移，单次最多 8000 字符。"""
        allowed = set(getattr(state, "attachment_ids", []) or [])
        if file_id not in allowed:
            return {"error": "NOT_ATTACHED", "message": "该文件不在本轮附件中"}
        return await files.read_attachment(file_id, offset=offset, limit=min(max(1, int(limit)), 8000))

    return [list_attachments, read_attachment]


def build_kb_tools(state, kb) -> list:
    """KB tools: persist an attachment, and read the body behind a retrieval hit."""

    async def promote_to_kb(
        file_id: str,
        scope: str = "private",
        category_id: str = "",
    ) -> dict[str, Any]:
        """把本轮附件保存到知识库；scope=private/shared，shared 仅管理员。"""
        if file_id not in set(getattr(state, "attachment_ids", []) or []):
            return {"error": "NOT_ATTACHED", "message": "该文件不在本轮附件中"}
        if scope not in {"private", "shared"}:
            return {"error": "VALIDATION_ERROR", "message": "scope 只能是 private 或 shared"}
        try:
            role = await kb.role_for(state.app_user_id, state.tenant_id)
            result = await kb.promote(
                file_id,
                tenant_id=state.tenant_id,
                app_user_id=state.app_user_id,
                role=role,
                scope=scope,
                category_id=category_id or None,
            )
        except Exception as exc:  # noqa: BLE001 - tool errors go back to the model
            return {"error": "PROMOTE_FAILED", "message": str(exc)[:300]}
        doc = result.get("doc") or {}
        return {
            "docId": doc.get("doc_id"),
            "status": doc.get("index_status") or "pending",
            "scope": doc.get("scope") or scope,
            "dedupe": bool(result.get("dedupe")),
            "instruction": "文件已进入知识库索引队列；告诉用户已保存，不要声称已完成向量索引。",
        }

    async def read_knowledge(uri: str, offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        """读取知识库正文：uri 取自 search_knowledge 结果的 uri 或 documentUri。

        传文档目录 uri 返回分节清单；传分节 uri 返回正文（offset 为字符偏移，单次最多 8000 字符）。
        """
        clean = (uri or "").strip()
        if not clean:
            return {"error": "VALIDATION_ERROR", "message": "uri 不能为空"}
        try:
            scope = await kb.scope_for_uri(
                tenant_id=state.tenant_id, app_user_id=state.app_user_id, uri=clean
            )
            return await kb.read_uri(
                tenant_id=state.tenant_id,
                app_user_id=state.app_user_id,
                scope=scope,
                uri=clean,
                offset=max(0, int(offset or 0)),
                limit=min(max(1, int(limit or 4000)), 8000),
            )
        except GpaError as exc:
            return {"error": exc.code, "uri": clean, "message": exc.message}
        except Exception as exc:  # noqa: BLE001 - tool errors go back to the model
            logger.warning("read_knowledge failed (%s): %s", clean, exc)
            return {"error": "READ_FAILED", "uri": clean, "message": str(exc)[:300]}

    return [promote_to_kb, read_knowledge]
