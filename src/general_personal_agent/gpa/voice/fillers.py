"""Deterministic voice fillers: cover the silent gap before a slow turn.

The Personal Agent still owns the answer; the adapter only plays a short,
pre-synthesised phrase so a search/task turn does not feel dead for seconds.
"""

from __future__ import annotations

import re

REALTIME_RE = re.compile(
    r"天气|气温|温度|下雨|降雨|台风|空气质量|雾霾|新闻|热搜|汇率|股价|股票|油价|金价|比分|赛况|发布会|最新消息"
)
TASK_RE = re.compile(
    r"(?:帮我|给我|请)?\s*(?:生成|做|画|写|制作|创建|搞|来)(?:一?个|一?张|一?份|下)?[^，。！？]{0,12}"
    r"(?:海报|图片|照片|PPT|ppt|文档|邀请函|方案|报告|视频)"
)
RETRIEVAL_RE = re.compile(r"查一下|查查|搜一下|搜索|找一下|资料|知识库|还记得|上次|之前说|记忆|总结")

FILLER_REALTIME = "稍等，我查一下。"
FILLER_TASK = "好的，我安排一下。"
FILLER_RETRIEVAL = "我想一下。"

# Short tool names -> spoken progress label / one-shot filler.
TOOL_LABELS = {
    "web_search": "正在查资料",
    "search_memory": "正在回忆",
    "search_knowledge": "正在查知识库",
    "submit_task": "正在创建任务",
    "get_task": "正在查任务",
    "list_tasks": "正在查任务",
    "read_artifact": "正在读取文件",
}
TOOL_FILLERS = {
    "web_search": "我查一下资料。",
    "search_memory": "我想一下。",
    "search_knowledge": "我查一下知识库。",
    "submit_task": "好的，我来安排。",
}


def filler_for(text: str) -> str:
    """Return a filler phrase for latency-prone intents, or ``""`` for chat."""
    value = (text or "").strip()
    if not value:
        return ""
    if REALTIME_RE.search(value):
        return FILLER_REALTIME
    if TASK_RE.search(value):
        return FILLER_TASK
    if RETRIEVAL_RE.search(value):
        return FILLER_RETRIEVAL
    return ""


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name or "", "")


def tool_filler(name: str) -> str:
    return TOOL_FILLERS.get(name or "", "")
