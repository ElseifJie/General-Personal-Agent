from general_personal_agent.voice.sentences import SentenceSplitter


def test_split_on_sentence_end():
    splitter = SentenceSplitter()
    assert splitter.feed("今天天气不错。") == ["今天天气不错。"]
    assert splitter.feed("我们去公园吧！") == ["我们去公园吧！"]
    assert splitter.flush() == ""


def test_incremental_tokens_are_buffered():
    splitter = SentenceSplitter()
    out = []
    for char in "你好，我是小万。有什么可以帮你？":
        out.extend(splitter.feed(char))
    assert out == ["你好，我是小万。", "有什么可以帮你？"]


def test_first_sentence_soft_split_when_too_long():
    splitter = SentenceSplitter(first_max=12)
    out = splitter.feed("这句话很长，需要尽早切分，后面还有内容。")
    assert out
    assert len(out[0]) <= 12
    assert out[0].endswith("，")


def test_flush_returns_tail():
    splitter = SentenceSplitter()
    splitter.feed("没有标点的一句话")
    assert splitter.flush() == "没有标点的一句话"
    assert splitter.flush() == ""


def test_has_readable_filters_punctuation_only():
    from general_personal_agent.voice.sentences import has_readable

    assert has_readable("你好呀") is True
    assert has_readable("？") is False
    assert has_readable("。。！") is False
    assert has_readable("") is False
    assert has_readable("A1眼镜") is True


def test_splitter_tail_can_be_punctuation_only():
    from general_personal_agent.voice.sentences import SentenceSplitter

    splitter = SentenceSplitter(first_max=20)
    out = []
    for chunk in ["你好呀，今天有什么想聊的或者需要我帮忙的？"]:
        out += splitter.feed(chunk)
    tail = splitter.flush()
    # the 20-char first-sentence clip leaves a bare "？" as the tail
    assert tail == "？" or tail == ""


def test_splitter_never_cuts_a_clause_in_half():
    from general_personal_agent.voice.sentences import SentenceSplitter

    splitter = SentenceSplitter(first_max=20)
    out = splitter.feed("我叫 GPA，是 VeADK 团队开发的个人助理。")
    assert out[0] == "我叫 GPA，"
    assert all(piece.endswith(("。", "，", "！", "？", "；")) for piece in out)


def test_splitter_caps_a_clause_without_any_punctuation():
    from general_personal_agent.voice.sentences import SentenceSplitter

    splitter = SentenceSplitter(first_max=10, max_chars=30)
    out = splitter.feed("这是一个完全没有标点符号的很长很长的句子需要被切成几段才行否则会一直等下去")
    assert out
    assert len(out[0]) <= 30


def test_chunk_text_keeps_a_short_answer_in_one_request():
    from general_personal_agent.voice.sentences import chunk_text

    text = "我叫 GPA，是 VeADK 团队开发的个人助理。有什么需要我帮忙的？"
    assert chunk_text(text) == [text]


def test_chunk_text_packs_long_answers_at_sentence_bounds():
    from general_personal_agent.voice.sentences import chunk_text

    text = "第一句话。" * 30
    chunks = chunk_text(text, max_chars=50)
    assert all(len(chunk) <= 50 for chunk in chunks)
    assert "".join(chunks) == text


def test_splitter_merges_too_short_sentences_instead_of_stalling():
    from general_personal_agent.voice.sentences import SentenceSplitter

    splitter = SentenceSplitter(first_max=20)
    assert splitter.feed("好。") == []
    out = splitter.feed("今天天气不错。")
    assert out == ["好。今天天气不错。"]
    assert splitter.flush() == ""
