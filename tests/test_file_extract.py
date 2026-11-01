"""Text extraction and chunking unit tests."""

from __future__ import annotations

from general_personal_agent.gpa.files.extract import extract_text, safe_name, split_chunks


def test_text_and_markdown_extraction_preserves_content():
    result = extract_text("notes.md", "text/markdown", "# 标题\n\n正文".encode())
    assert result.text.startswith("# 标题")
    assert result.error is None


def test_csv_is_normalized_to_tabs():
    result = extract_text("data.csv", "text/csv", b"a,b\n1,2\n")
    assert "a\tb" in result.text


def test_unknown_binary_is_skipped_not_failed():
    result = extract_text("voice.mp3", "audio/mpeg", b"\x00\x01")
    assert result.skipped is True
    assert result.text == ""


def test_chunks_cover_every_character_without_gaps():
    text = ("段落\n" * 900).strip()
    chunks = split_chunks(text)
    assert chunks
    assert chunks[0]["char_start"] == 0
    assert chunks[-1]["char_end"] == len(text)
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert left["char_end"] == right["char_start"]


def test_safe_name_strips_paths_and_controls():
    assert safe_name("../../etc/passwd") == "passwd"
    assert "\x00" not in safe_name("a\x00b.txt")
