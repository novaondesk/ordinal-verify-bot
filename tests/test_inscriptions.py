"""
Tests for fetch_inscriptions against the Ordiscan API.

Hiro's /ordinals/v1/* was deprecated 2026-03-09; the bot was migrated to
Ordiscan's /v1/address/{addr}/inscriptions which is free-tier-friendly and
returns a `collection_slug` per item (used by the holder-rule feature).

Network tests skip via:  pytest -m "not network"
"""

from __future__ import annotations

import asyncio
import importlib
import os

import httpx
import pytest


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "verifications.db"))
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token-for-import")
    import ordinal_verify_bot

    importlib.reload(ordinal_verify_bot)
    return ordinal_verify_bot


# ---- offline: behavior without an API key --------------------------------


def test_returns_empty_when_no_api_key(bot, monkeypatch):
    """Bot must not 401 the user — it should silently skip the lookup so
    /verify still completes and just shows zero inscriptions."""
    monkeypatch.setattr(bot, "ORDISCAN_API_KEY", None)
    result = asyncio.run(bot.fetch_inscriptions("bc1qexample", max_pages=1))
    assert result == []


def test_no_network_call_when_key_missing(bot, monkeypatch):
    """Belt-and-braces: if ORDISCAN_API_KEY is unset, fetch_inscriptions must
    not even open a client. Stub httpx to blow up if it's touched."""
    monkeypatch.setattr(bot, "ORDISCAN_API_KEY", None)

    class _Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("httpx.AsyncClient created with no API key")

    monkeypatch.setattr(bot.httpx, "AsyncClient", _Boom)
    asyncio.run(bot.fetch_inscriptions("bc1qexample"))  # must not raise


# ---- network: the live endpoint --------------------------------------------


# Owner of inscription #0 (the genesis address) — known mainnet holder with a
# small, stable inscription set, verified live during migration.
KNOWN_HOLDER = "bc1pd96xzyue7yvjf24cmu07xasezg3jpm5tyfem4txad5ke2jas4m7qkhe7dy"
ORDISCAN_BASE = "https://api.ordiscan.com"


@pytest.mark.network
def test_ordiscan_endpoint_is_reachable():
    """Without a key Ordiscan returns 402 (their x402 challenge-auth scheme),
    not 404/410. Proves the path is correct and the service is up."""
    r = httpx.get(
        f"{ORDISCAN_BASE}/v1/address/{KNOWN_HOLDER}/inscriptions",
        timeout=15,
    )
    assert r.status_code in (401, 402, 403), (
        f"unexpected status {r.status_code}: {r.text[:200]}"
    )


@pytest.mark.network
def test_fetch_inscriptions_raises_with_bogus_key(bot, monkeypatch):
    """If a deployer sets a *bogus* key (not just missing), the call should
    surface as HTTPStatusError so submit_cmd's existing except clause kicks
    in and degrades to 'no inscriptions'."""
    monkeypatch.setattr(bot, "ORDISCAN_API_KEY", "obviously-not-a-real-key")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        asyncio.run(bot.fetch_inscriptions(KNOWN_HOLDER))
    assert exc.value.response.status_code in (401, 402, 403)


@pytest.mark.network
def test_fetch_inscriptions_normalizes_response_shape(bot, monkeypatch):
    """With the real key, fetch_inscriptions should return our normalized
    shape: every item has id, number, collection_slug, content_type. Skip if
    no real key is configured in the environment."""
    real_key = os.getenv("ORDISCAN_API_KEY")
    if not real_key:
        pytest.skip("ORDISCAN_API_KEY not set in environment — skipping live read")

    monkeypatch.setattr(bot, "ORDISCAN_API_KEY", real_key)
    result = asyncio.run(bot.fetch_inscriptions(KNOWN_HOLDER, max_pages=2))
    assert isinstance(result, list)
    if result:
        first = result[0]
        for k in ("id", "number", "collection_slug", "content_type"):
            assert k in first, f"missing normalized key: {k}"
