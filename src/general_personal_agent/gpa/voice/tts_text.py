"""Make model output safe to read aloud: strip Markdown, URLs and emoji.

Text answers keep their Markdown for the workbench; only the TTS path runs
through this sanitizer, so a voice reply never reads "星号星号" or a raw URL.
"""

from __future__ import annotations

import re

_EMOJI_RE = re.compile(
    "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\uFE0F\u2B50\u2705\u274C]+"
)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_BULLET_RE = re.compile(r"(?m)^\s*[-*+]\s+")
_QUOTE_RE = re.compile(r"(?m)^\s*>\s?")
_RULE_RE = re.compile(r"(?m)^\s*([-*_])\1{2,}\s*$")
_HTML_RE = re.compile(r"<[^>]+>")
_ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_SPACES_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


def sanitize_for_tts(text: str) -> str:
    """Return a speakable plain-text version of ``text`` (idempotent)."""
    if not text:
        return ""
    clean = _FENCE_RE.sub(" ", text)
    clean = _INLINE_CODE_RE.sub(r"\1", clean)
    clean = _IMAGE_RE.sub(" ", clean)
    clean = _LINK_RE.sub(r"\1", clean)
    clean = _REF_LINK_RE.sub(r"\1", clean)
    clean = _URL_RE.sub(" ", clean)
    clean = _HEADING_RE.sub("", clean)
    clean = _BULLET_RE.sub("", clean)
    clean = _QUOTE_RE.sub("", clean)
    clean = _RULE_RE.sub(" ", clean)
    clean = _HTML_RE.sub(" ", clean)
    clean = clean.replace("**", "").replace("__", "").replace("~~", "")
    clean = _ITALIC_RE.sub(r"\1", clean)
    clean = clean.replace("[", " ").replace("]", " ")
    clean = clean.replace("|", "，")
    clean = _EMOJI_RE.sub(" ", clean)
    clean = _SPACES_RE.sub(" ", clean)
    clean = _BLANK_RE.sub("\n\n", clean)
    return clean.strip()
