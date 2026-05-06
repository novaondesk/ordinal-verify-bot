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


def test_evaluate_meta_collection_match(bot):
    """Pre-Sept-2023 collections (e.g. Ordinal Pizza OG) match against an
    Ordinals Wallet curated id-set passed in via meta_lists."""
    bot.add_role_rule("g", "meta_collection", "ordinal-pizza-og", "pizzaowner")
    held = [_insc(id_="d22d88cd...i0"), _insc(id_="unrelated...i0")]
    meta = {"ordinal-pizza-og": {"d22d88cd...i0", "another...i0"}}
    assert bot.evaluate_rules("g", held, meta) == ["pizzaowner"]


def test_evaluate_meta_collection_miss(bot):
    bot.add_role_rule("g", "meta_collection", "ordinal-pizza-og", "pizzaowner")
    held = [_insc(id_="orphan...i0")]
    meta = {"ordinal-pizza-og": {"a...i0", "b...i0"}}
    assert bot.evaluate_rules("g", held, meta) == []


def test_evaluate_meta_collection_no_meta_lists_treated_as_miss(bot):
    """If the prefetch failed and no IDs were provided, the rule simply
    doesn't match — bot keeps working, just doesn't grant the role."""
    bot.add_role_rule("g", "meta_collection", "ordinal-pizza-og", "pizzaowner")
    held = [_insc(id_="d22d88cd...i0")]
    assert bot.evaluate_rules("g", held) == []
    assert bot.evaluate_rules("g", held, {}) == []


# ---- role sync helpers ----------------------------------------------------


def test_managed_role_ids_includes_all_rules(bot):
    bot.add_role_rule("g", "parent", "P", "role-A")
    bot.add_role_rule("g", "collection", "frogs", "role-B")
    bot.add_role_rule("g", "meta_collection", "ordinal-pizza-og", "role-C")
    assert bot.managed_role_ids("g") == {"role-A", "role-B", "role-C"}


def test_managed_role_ids_includes_verified_role_when_set(bot, monkeypatch):
    monkeypatch.setattr(bot, "VERIFIED_ROLE_ID", 999)
    bot.add_role_rule("g", "parent", "P", "role-A")
    assert bot.managed_role_ids("g") == {"role-A", "999"}


def test_managed_role_ids_per_guild(bot):
    bot.add_role_rule("guild-A", "parent", "P", "role-A")
    bot.add_role_rule("guild-B", "parent", "P", "role-B")
    assert bot.managed_role_ids("guild-A") == {"role-A"}
    assert bot.managed_role_ids("guild-B") == {"role-B"}


def test_role_diff_adds_missing_target_roles(bot):
    add, remove = bot.compute_role_diff(
        member_role_ids={"existing"},
        target_role_ids={"existing", "new-A", "new-B"},
        managed={"new-A", "new-B"},
    )
    assert add == {"new-A", "new-B"}
    assert remove == set()


def test_role_diff_removes_managed_roles_no_longer_held(bot):
    """User had role-A from a prior /submit but sold the ordinal — sync removes it."""
    add, remove = bot.compute_role_diff(
        member_role_ids={"role-A", "role-B"},
        target_role_ids={"role-B"},
        managed={"role-A", "role-B"},
    )
    assert add == set()
    assert remove == {"role-A"}


def test_role_diff_never_touches_unmanaged_roles(bot):
    """Roles outside the managed set (granted by other bots, mods, etc) MUST
    NOT be removed even if not in the target set — that's the whole safety
    net of the sync feature."""
    add, remove = bot.compute_role_diff(
        member_role_ids={"some-mod-role", "another-bot-role"},
        target_role_ids=set(),
        managed=set(),
    )
    assert add == set()
    assert remove == set()


def test_role_diff_idempotent(bot):
    """Running sync twice with no holdings change → zero diff."""
    add, remove = bot.compute_role_diff(
        member_role_ids={"role-A", "role-B", "unrelated"},
        target_role_ids={"role-A", "role-B"},
        managed={"role-A", "role-B"},
    )
    assert add == set()
    assert remove == set()


def test_role_diff_handles_target_outside_managed(bot):
    """If a target role isn't in managed (shouldn't happen in practice but
    defensible), we still ADD it — managed only restricts removals."""
    add, remove = bot.compute_role_diff(
        member_role_ids=set(),
        target_role_ids={"role-A"},
        managed=set(),
    )
    assert add == {"role-A"}
    assert remove == set()


def test_all_verified_discord_ids_dedupes_across_addresses(bot):
    """Same discord_id verifying multiple addresses → returned once."""
    import time
    conn = bot._db()
    conn.execute("INSERT INTO verifications VALUES ('user-1', 'addr-A', ?)", (int(time.time()),))
    conn.execute("INSERT INTO verifications VALUES ('user-1', 'addr-B', ?)", (int(time.time()),))
    conn.execute("INSERT INTO verifications VALUES ('user-2', 'addr-C', ?)", (int(time.time()),))
    conn.commit()
    conn.close()
    assert sorted(bot.all_verified_discord_ids()) == ["user-1", "user-2"]
