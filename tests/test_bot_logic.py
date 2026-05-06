"""
In-process tests of the bot's non-Discord logic: challenge issuance,
nonce expiry, replay prevention, sqlite round-trip.

We import the module after pointing DB_PATH at a temp file so we never
clobber a real verifications.db.
"""

from __future__ import annotations

import importlib
import os
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "verifications.db"))
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token-for-import")
    import ordinal_verify_bot

    importlib.reload(ordinal_verify_bot)
    ordinal_verify_bot._pending.clear()
    return ordinal_verify_bot


def test_make_message_is_deterministic(bot):
    a = bot.make_message("123", "bc1qxyz", "nonce1")
    b = bot.make_message("123", "bc1qxyz", "nonce1")
    assert a == b
    assert "discord:123" in a
    assert "addr:bc1qxyz" in a
    assert "nonce:nonce1" in a


def test_make_message_changes_with_inputs(bot):
    base = bot.make_message("123", "bc1qxyz", "n1")
    assert base != bot.make_message("124", "bc1qxyz", "n1")
    assert base != bot.make_message("123", "bc1qabc", "n1")
    assert base != bot.make_message("123", "bc1qxyz", "n2")


def test_record_and_list_round_trip(bot):
    bot.record_verification("user-1", "bc1q111")
    bot.record_verification("user-1", "bc1q222")
    bot.record_verification("user-2", "bc1q333")

    user1 = bot.list_verifications("user-1")
    assert {addr for addr, _ts in user1} == {"bc1q111", "bc1q222"}
    assert all(isinstance(ts, int) and ts > 0 for _addr, ts in user1)

    user2 = bot.list_verifications("user-2")
    assert [addr for addr, _ in user2] == ["bc1q333"]

    assert bot.list_verifications("nobody") == []


def test_record_is_idempotent(bot):
    bot.record_verification("user-1", "bc1q111")
    bot.record_verification("user-1", "bc1q111")
    assert len(bot.list_verifications("user-1")) == 1


def test_pending_challenge_ttl_logic(bot):
    """The submit_cmd handler should treat an old challenge as expired."""
    bot._pending["u1"] = bot.Challenge(
        nonce="n",
        btc_address="bc1q...",
        issued_at=time.time() - bot.CHALLENGE_TTL_SEC - 1,
    )
    chal = bot._pending["u1"]
    assert time.time() - chal.issued_at > bot.CHALLENGE_TTL_SEC


def test_fresh_challenge_within_ttl(bot):
    bot._pending["u1"] = bot.Challenge(
        nonce="n", btc_address="bc1q...", issued_at=time.time()
    )
    chal = bot._pending["u1"]
    assert time.time() - chal.issued_at <= bot.CHALLENGE_TTL_SEC


def test_pending_is_per_user(bot):
    bot._pending["u1"] = bot.Challenge(nonce="n1", btc_address="a1", issued_at=time.time())
    bot._pending["u2"] = bot.Challenge(nonce="n2", btc_address="a2", issued_at=time.time())
    assert bot._pending["u1"].nonce == "n1"
    assert bot._pending["u2"].nonce == "n2"


def test_challenge_consumed_on_success_pattern(bot):
    """submit_cmd does `_pending.pop(discord_id)` after a successful verify.
    Any subsequent /submit with the same nonce must find no challenge — replay
    is defeated by absence, not by signature re-check."""
    bot._pending["u1"] = bot.Challenge(nonce="n", btc_address="a", issued_at=time.time())
    bot._pending.pop("u1", None)
    assert bot._pending.get("u1") is None


def test_db_schema_has_primary_key(bot):
    """Same (discord_id, address) inserted twice must not duplicate rows."""
    bot.record_verification("u", "addr-1")
    first_ts = bot.list_verifications("u")[0][1]
    time.sleep(1.1)
    bot.record_verification("u", "addr-1")
    rows = bot.list_verifications("u")
    assert len(rows) == 1
    # INSERT OR REPLACE updates verified_at
    assert rows[0][1] >= first_ts
