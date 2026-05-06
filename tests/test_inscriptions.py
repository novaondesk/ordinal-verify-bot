"""
Tests for fetch_inscriptions against the Xverse Ordinals API.

Hiro's /ordinals/v1/* was deprecated 2026-03-09; the bot was migrated to
Xverse's /v1/ordinals/address/{addr}/inscriptions in this branch.

Network tests skip via:  pytest -m "not network"
"""

from __future__ import annotations

import asyncio
import importlib

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
    monkeypatch.setattr(bot, "XVERSE_API_KEY", None)
    result = asyncio.run(bot.fetch_inscriptions("bc1qexample", max_pages=1))
    assert result == []


def test_no_network_call_when_key_missing(bot, monkeypatch):
    """Belt-and-braces: if XVERSE_API_KEY is unset, fetch_inscriptions must
    not even open a client. Stub httpx to blow up if it's touched."""
    monkeypatch.setattr(bot, "XVERSE_API_KEY", None)

    class _Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("httpx.AsyncClient created with no API key")

    monkeypatch.setattr(bot.httpx, "AsyncClient", _Boom)
    asyncio.run(bot.fetch_inscriptions("bc1qexample"))  # must not raise


# ---- network: the live endpoint --------------------------------------------


KNOWN_HOLDER = "bc1pmc6q88wytmu29ppwgr5z3uw6c6av4xyfu8ftq7zydekzh90zedyqg6dl74"
XVERSE_BASE = "https://api.secretkeylabs.io"


@pytest.mark.network
def test_xverse_endpoint_is_reachable():
    """Without a key we expect an auth/payment error (Xverse uses HTTP 402
    challenge auth), not 404/410. Proves the path is correct and the service
    is up — and prevents silent regression if Xverse moves the endpoint."""
    r = httpx.get(
        f"{XVERSE_BASE}/v1/ordinals/address/{KNOWN_HOLDER}/inscriptions",
        timeout=15,
    )
    assert r.status_code in (401, 402, 403), (
        f"unexpected status {r.status_code}: {r.text[:200]}"
    )


@pytest.mark.network
def test_fetch_inscriptions_raises_without_key_when_called_directly(bot, monkeypatch):
    """If a deployer sets a *bogus* key (not just missing), the call should
    surface as HTTPStatusError so submit_cmd's existing except clause kicks
    in and degrades to 'no inscriptions'."""
    monkeypatch.setattr(bot, "XVERSE_API_KEY", "obviously-not-a-real-key")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        asyncio.run(bot.fetch_inscriptions(KNOWN_HOLDER))
    assert exc.value.response.status_code in (401, 403)
