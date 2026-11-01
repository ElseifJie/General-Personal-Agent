import time

from general_personal_agent.auth import check_pin, sign_session, verify_session

SECRET = "test-secret"


def test_session_roundtrip():
    token = sign_session("alice", "user", SECRET, 60)
    payload = verify_session(token, SECRET)
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["role"] == "user"


def test_tampered_token_rejected():
    token = sign_session("alice", "user", SECRET, 60)
    assert verify_session(token + "x", SECRET) is None
    assert verify_session(token, "other-secret") is None


def test_expired_token_rejected():
    token = sign_session("alice", "user", SECRET, -1)
    assert verify_session(token, SECRET) is None


def test_pin_compare():
    assert check_pin("123456", "123456")
    assert not check_pin("123456", "654321")
    assert not check_pin("", "")


def test_expiry_is_in_future():
    token = sign_session("bob", "admin", SECRET, 600)
    payload = verify_session(token, SECRET)
    assert payload is not None
    assert payload["exp"] > int(time.time())


def test_session_token_carries_iat():
    token = sign_session("alice", "user", "s3cret", 60)
    payload = verify_session(token, "s3cret")
    assert payload["sub"] == "alice"
    assert payload["iat"] <= time.time()
    assert payload["exp"] > payload["iat"]


def test_sliding_refresh_threshold():
    """过半生命周期才续签；新 token 与频繁请求不续签。"""
    from general_personal_agent.auth import (
        refresh_session_token,
        should_refresh_session,
    )

    ttl = 600
    fresh = verify_session(sign_session("alice", "user", "k", ttl), "k")
    assert should_refresh_session(fresh, ttl) is False       # 刚签发：不续

    aged = dict(fresh, iat=int(time.time()) - ttl // 2 - 5)  # 过半：续
    assert should_refresh_session(aged, ttl) is True

    legacy = {k: v for k, v in fresh.items() if k != "iat"}   # 无 iat 旧 token：续
    assert should_refresh_session(legacy, ttl) is True

    renewed = verify_session(refresh_session_token(aged, "k", ttl), "k")
    assert renewed["sub"] == aged["sub"]
    assert renewed["exp"] - renewed["iat"] == ttl             # 全新生命周期
    assert should_refresh_session(renewed, ttl) is False
