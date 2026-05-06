"""
Tests for the role-rule storage layer + evaluator.

These don't touch Discord or the network — they exercise the pure-Python
helpers (add/remove/list/evaluate) against a temp SQLite DB.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "verifications.db"))
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token-for-import")
    import ordinal_verify_bot

    importlib.reload(ordinal_verify_bot)
    return ordinal_verify_bot


# ---- storage layer --------------------------------------------------------


def test_add_then_list_round_trip(bot):
    rid = bot.add_role_rule("guild-1", "parent", "abc...i0", "role-A")
    rules = bot.list_role_rules("guild-1")
    assert len(rules) == 1
    rid2, mtype, mvalue, role_id = rules[0]
    assert rid2 == rid
    assert mtype == "parent"
    assert mvalue == "abc...i0"
    assert role_id == "role-A"


def test_add_is_idempotent(bot):
    """Same (guild, type, value, role) twice should not create a duplicate row."""
    a = bot.add_role_rule("g", "inscription", "x", "r")
    b = bot.add_role_rule("g", "inscription", "x", "r")
    assert a == b
    assert len(bot.list_role_rules("g")) == 1


def test_rules_are_per_guild(bot):
    bot.add_role_rule("guild-1", "parent", "p", "r1")
    bot.add_role_rule("guild-2", "parent", "p", "r2")
    assert len(bot.list_role_rules("guild-1")) == 1
    assert len(bot.list_role_rules("guild-2")) == 1
    assert bot.list_role_rules("guild-1")[0][3] == "r1"
    assert bot.list_role_rules("guild-2")[0][3] == "r2"


def test_remove_only_within_guild(bot):
    rid = bot.add_role_rule("guild-1", "parent", "p", "r1")
    # wrong guild can't delete
    assert bot.remove_role_rule(rid, "guild-2") is False
    assert len(bot.list_role_rules("guild-1")) == 1
    # right guild can
    assert bot.remove_role_rule(rid, "guild-1") is True
    assert bot.list_role_rules("guild-1") == []


def test_remove_unknown_returns_false(bot):
    assert bot.remove_role_rule(9999, "any-guild") is False


def test_invalid_match_type_rejected(bot):
    with pytest.raises(ValueError):
        bot.add_role_rule("g", "not-a-real-type", "v", "r")


# ---- evaluator ------------------------------------------------------------


def _insc(id_=None, parent=None, slug=None, num=1):
    """Build a normalized inscription dict like fetch_inscriptions emits."""
    return {
        "id": id_ or "default-id",
        "number": num,
        "collection_slug": slug,
        "parent_inscription_id": parent,
        "content_type": "image/png",
        "owner_address": "bc1qx",
    }


def test_evaluate_no_rules_returns_empty(bot):
    assert bot.evaluate_rules("g", [_insc(id_="a")]) == []


def test_evaluate_no_inscriptions_returns_empty(bot):
    bot.add_role_rule("g", "inscription", "a", "role-A")
    assert bot.evaluate_rules("g", []) == []


def test_evaluate_inscription_match(bot):
    bot.add_role_rule("g", "inscription", "abc...i0", "role-A")
    granted = bot.evaluate_rules("g", [_insc(id_="abc...i0")])
    assert granted == ["role-A"]


def test_evaluate_inscription_miss(bot):
    bot.add_role_rule("g", "inscription", "abc...i0", "role-A")
    granted = bot.evaluate_rules("g", [_insc(id_="other...i0")])
    assert granted == []


def test_evaluate_parent_match(bot):
    """The user's specific use case: holding any child of a known parent."""
    bot.add_role_rule("g", "parent", "PIZZA-PARENT-i0", "pizzaowner-role")
    held = [
        _insc(id_="child-1-i0", parent="PIZZA-PARENT-i0"),
        _insc(id_="unrelated-i0", parent="OTHER-PARENT-i0"),
    ]
    granted = bot.evaluate_rules("g", held)
    assert granted == ["pizzaowner-role"]


def test_evaluate_parent_miss(bot):
    bot.add_role_rule("g", "parent", "PIZZA-PARENT-i0", "pizzaowner-role")
    held = [_insc(id_="orphan-i0", parent=None)]
    assert bot.evaluate_rules("g", held) == []


def test_evaluate_collection_match(bot):
    bot.add_role_rule("g", "collection", "bitcoin-frogs", "frog-role")
    held = [_insc(id_="x", slug="bitcoin-frogs")]
    assert bot.evaluate_rules("g", held) == ["frog-role"]


def test_evaluate_multiple_rules_grant_all_matches(bot):
    bot.add_role_rule("g", "parent", "P1", "role-1")
    bot.add_role_rule("g", "collection", "frogs", "role-2")
    bot.add_role_rule("g", "inscription", "iCANTBEFOUND", "role-3")
    held = [
        _insc(id_="x1", parent="P1"),
        _insc(id_="x2", slug="frogs"),
    ]
    granted = sorted(bot.evaluate_rules("g", held))
    assert granted == ["role-1", "role-2"]


def test_evaluate_dedupes_when_one_inscription_matches_multiple_rules(bot):
    """If two rules both target the same role, the user shouldn't see it twice."""
    bot.add_role_rule("g", "parent", "P", "role-A")
    bot.add_role_rule("g", "collection", "slug", "role-A")
    held = [_insc(id_="x", parent="P", slug="slug")]
    granted = bot.evaluate_rules("g", held)
    assert granted == ["role-A"]


def test_evaluate_isolated_per_guild(bot):
    bot.add_role_rule("guild-A", "parent", "P", "role-A")
    bot.add_role_rule("guild-B", "parent", "P", "role-B")
    held = [_insc(id_="x", parent="P")]
    assert bot.evaluate_rules("guild-A", held) == ["role-A"]
    assert bot.evaluate_rules("guild-B", held) == ["role-B"]
