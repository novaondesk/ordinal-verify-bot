#!/usr/bin/env python3
"""
Ordinal Verify Bot

Discord bot that proves a user controls a Bitcoin address (and lists any
Ordinal inscriptions held there) via BIP-322 signed messages.

Flow:
    /verify  address:<btc_addr>      -> bot returns a single-use challenge
    /submit  signature:<base64_sig>  -> bot verifies & records the binding
    /whoami                          -> show this user's verified addresses

All responses are ephemeral so signatures and addresses are never visible
to the rest of the channel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass

import bip322
import discord
import httpx
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ordinal-verify-bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ORDISCAN_API = os.getenv("ORDISCAN_API", "https://api.ordiscan.com").rstrip("/")
ORDISCAN_API_KEY = os.getenv("ORDISCAN_API_KEY") or None
GUILD_ID = int(os.getenv("GUILD_ID", "0")) or None
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0")) or None
DB_PATH = os.getenv("DB_PATH", "verifications.db")
SIGN_PAGE_URL = os.getenv("SIGN_PAGE_URL") or None
CHALLENGE_TTL_SEC = 300

# Slash commands carry their own data — no privileged intents needed.
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ----- challenge state ---------------------------------------------------

@dataclass
class Challenge:
    nonce: str
    btc_address: str
    issued_at: float


# discord_id -> Challenge. In-memory by design: a restart invalidates all
# pending challenges, which is the safe default for nonces.
_pending: dict[str, Challenge] = {}


def make_message(discord_id: str, btc_address: str, nonce: str) -> str:
    """Challenge string the user signs. Binds discord identity + address + nonce."""
    return f"OrdinalVerifyBot|discord:{discord_id}|addr:{btc_address}|nonce:{nonce}"


# ----- storage -----------------------------------------------------------

RULE_MATCH_TYPES = ("inscription", "parent", "collection")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS verifications (
            discord_id  TEXT NOT NULL,
            btc_address TEXT NOT NULL,
            verified_at INTEGER NOT NULL,
            PRIMARY KEY (discord_id, btc_address)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS role_rules (
            rule_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT NOT NULL,
            match_type  TEXT NOT NULL,
            match_value TEXT NOT NULL,
            role_id     TEXT NOT NULL,
            UNIQUE(guild_id, match_type, match_value, role_id)
        )
        """
    )
    return conn


def record_verification(discord_id: str, btc_address: str) -> None:
    conn = _db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO verifications(discord_id, btc_address, verified_at) VALUES (?,?,?)",
            (discord_id, btc_address, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def list_verifications(discord_id: str) -> list[tuple[str, int]]:
    conn = _db()
    try:
        return conn.execute(
            "SELECT btc_address, verified_at FROM verifications WHERE discord_id=? ORDER BY verified_at DESC",
            (discord_id,),
        ).fetchall()
    finally:
        conn.close()


def add_role_rule(guild_id: str, match_type: str, match_value: str, role_id: str) -> int:
    """Returns the new rule_id, or the existing one if the rule already exists."""
    if match_type not in RULE_MATCH_TYPES:
        raise ValueError(f"match_type must be one of {RULE_MATCH_TYPES}")
    conn = _db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO role_rules(guild_id, match_type, match_value, role_id) VALUES (?,?,?,?)",
            (str(guild_id), match_type, match_value, str(role_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT rule_id FROM role_rules WHERE guild_id=? AND match_type=? AND match_value=? AND role_id=?",
            (str(guild_id), match_type, match_value, str(role_id)),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def remove_role_rule(rule_id: int, guild_id: str) -> bool:
    """Returns True if a row was deleted."""
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM role_rules WHERE rule_id=? AND guild_id=?",
            (rule_id, str(guild_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_role_rules(guild_id: str) -> list[tuple[int, str, str, str]]:
    conn = _db()
    try:
        return conn.execute(
            "SELECT rule_id, match_type, match_value, role_id FROM role_rules WHERE guild_id=? ORDER BY rule_id",
            (str(guild_id),),
        ).fetchall()
    finally:
        conn.close()


def evaluate_rules(guild_id: str, inscriptions: list[dict]) -> list[str]:
    """Return the de-duplicated list of role_ids to grant given the user's
    inscriptions and the guild's configured rules."""
    rules = list_role_rules(guild_id)
    if not rules or not inscriptions:
        return []
    granted: set[str] = set()
    for _rid, mtype, mvalue, role_id in rules:
        for insc in inscriptions:
            field = {
                "inscription": "id",
                "parent": "parent_inscription_id",
                "collection": "collection_slug",
            }[mtype]
            if insc.get(field) == mvalue:
                granted.add(role_id)
                break
    return list(granted)


# ----- ordinals lookup ---------------------------------------------------

async def fetch_inscription(inscription_id: str) -> dict | None:
    """Single-inscription lookup. Returns None if not found or no API key."""
    if not ORDISCAN_API_KEY:
        return None
    url = f"{ORDISCAN_API}/v1/inscription/{inscription_id}"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {ORDISCAN_API_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=headers)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("data")


async def count_children(parent_id: str) -> int:
    """How many children does this parent inscription have? Caps at 100 per
    page; we only check the first page to give admins a quick sanity signal."""
    if not ORDISCAN_API_KEY:
        return 0
    url = f"{ORDISCAN_API}/v1/inscriptions"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {ORDISCAN_API_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=headers, params={"parent": parent_id, "page": 1})
        r.raise_for_status()
        return len(r.json().get("data", []) or [])


async def fetch_inscriptions(address: str, max_pages: int = 10) -> list[dict]:
    """Ordiscan Ordinals API. Hiro's equivalent was deprecated 2026-03-09.

    Returns a flat list of inscription dicts each shaped:
        {id, number, collection_slug, content_type, owner_address}

    If ORDISCAN_API_KEY is unset, this returns [] rather than 401-ing — the
    bot keeps verifying ownership, it just can't display inscription counts
    or evaluate holder-rules.

    Pagination: Ordiscan caps each response at 100 inscriptions; we follow
    `?page=N` until we get a short page or hit `max_pages`.
    """
    if not ORDISCAN_API_KEY:
        log.warning("ORDISCAN_API_KEY not set — skipping inscription lookup")
        return []
    url = f"{ORDISCAN_API}/v1/address/{address}/inscriptions"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {ORDISCAN_API_KEY}"}
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for page in range(1, max_pages + 1):
            r = await client.get(url, headers=headers, params={"page": page})
            r.raise_for_status()
            items = r.json().get("data", []) or []
            for it in items:
                out.append({
                    "id": it.get("inscription_id"),
                    "number": it.get("inscription_number"),
                    "collection_slug": it.get("collection_slug"),
                    "parent_inscription_id": it.get("parent_inscription_id"),
                    "content_type": it.get("content_type"),
                    "owner_address": it.get("owner_address"),
                })
            if len(items) < 100:
                break
    return out


# ----- bot lifecycle -----------------------------------------------------

@bot.event
async def on_ready():
    log.info("Logged in as %s (id=%s)", bot.user, getattr(bot.user, "id", "?"))
    _db().close()
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        log.info("Synced %d slash commands", len(synced))
    except Exception:
        log.exception("Slash command sync failed")
    await bot.change_presence(activity=discord.Game(name="/verify"))


# ----- slash commands ----------------------------------------------------

class _ChallengeCopyView(discord.ui.View):
    """Buttons attached to /verify embeds.

    - "Sign with my wallet" (link button) — opens the static Sats Connect
      page hosted on GitHub Pages with addr+msg pre-filled. Wallet picks
      itself; on mobile, deep-links into Xverse/Leather/etc.
    - "Plain text" — falls back to posting the challenge as a bare ephemeral
      message for users whose wallet can't be invoked over Sats Connect (or
      who prefer the manual paste flow)."""

    def __init__(self, challenge_message: str, address: str):
        super().__init__(timeout=CHALLENGE_TTL_SEC)
        self._challenge = challenge_message
        if SIGN_PAGE_URL:
            url = (
                f"{SIGN_PAGE_URL}?addr={urllib.parse.quote(address)}"
                f"&msg={urllib.parse.quote(challenge_message)}"
            )
            self.add_item(
                discord.ui.Button(
                    label="🔐 Sign with my wallet",
                    style=discord.ButtonStyle.link,
                    url=url,
                )
            )

    @discord.ui.button(
        label="📋 Plain text (manual copy)",
        style=discord.ButtonStyle.secondary,
    )
    async def show_plain(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Bare text, no code fences, no embed — long-press selects the whole
        # message body in one shot on iOS and Android Discord clients.
        await interaction.response.send_message(self._challenge, ephemeral=True)


@bot.tree.command(
    name="verify",
    description="Begin Ordinal ownership verification — get a challenge to sign.",
)
@app_commands.describe(address="Your Bitcoin address (P2PKH, P2SH-P2WPKH, P2WPKH, or P2TR)")
async def verify_cmd(interaction: discord.Interaction, address: str):
    address = address.strip()
    if not (26 <= len(address) <= 90) or any(c.isspace() for c in address):
        await interaction.response.send_message(
            "That doesn't look like a Bitcoin address.", ephemeral=True
        )
        return

    nonce = secrets.token_urlsafe(16)
    discord_id = str(interaction.user.id)
    _pending[discord_id] = Challenge(nonce=nonce, btc_address=address, issued_at=time.time())
    msg = make_message(discord_id, address, nonce)

    if SIGN_PAGE_URL:
        instructions = (
            "**Easiest:** tap **🔐 Sign with my wallet** below — opens a page "
            "that invokes Xverse / Leather / OKX / Magic Eden / UniSat for you, "
            "no copy-paste needed.\n\n"
            "**Manual:** tap **📋 Plain text** to grab the message as text, "
            "paste it into your wallet's *Sign Message* feature, then run "
            "`/submit signature:<base64>` here within 5 minutes."
        )
    else:
        instructions = (
            "**1.** Open your wallet (Sparrow, Leather, Xverse, Unisat, …) and use "
            "**Sign Message** for the address below.\n"
            "**2.** Sign **exactly** the message below.\n"
            "**3.** Run `/submit signature:<base64>` within 5 minutes.\n\n"
            "📱 Tap **📋 Plain text** below for an easier mobile copy."
        )

    embed = discord.Embed(
        title="Sign this message with your Bitcoin wallet",
        description=f"{instructions}\n\n```\n{msg}\n```",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Address", value=f"`{address}`", inline=False)
    embed.add_field(
        name="Expires", value=f"<t:{int(time.time()) + CHALLENGE_TTL_SEC}:R>", inline=False
    )
    await interaction.response.send_message(
        embed=embed, view=_ChallengeCopyView(msg, address), ephemeral=True
    )


@bot.tree.command(
    name="submit",
    description="Submit your BIP-322 signature for the active challenge.",
)
@app_commands.describe(signature="Base64 BIP-322 signature for the challenge message")
async def submit_cmd(interaction: discord.Interaction, signature: str):
    discord_id = str(interaction.user.id)
    chal = _pending.get(discord_id)
    if not chal:
        await interaction.response.send_message(
            "No active challenge — run `/verify <address>` first.", ephemeral=True
        )
        return
    if time.time() - chal.issued_at > CHALLENGE_TTL_SEC:
        _pending.pop(discord_id, None)
        await interaction.response.send_message(
            "Challenge expired — run `/verify <address>` again.", ephemeral=True
        )
        return

    msg = make_message(discord_id, chal.btc_address, chal.nonce)
    await interaction.response.defer(ephemeral=True, thinking=True)

    # bip322.verify_simple_encoded returns None on success and raises VerificationError otherwise.
    # It's a Rust call; offload from the event loop just in case.
    try:
        await asyncio.to_thread(
            bip322.verify_simple_encoded, chal.btc_address, msg, signature.strip()
        )
    except bip322.VerificationError as e:
        await interaction.followup.send(f"❌ Invalid signature: `{e}`", ephemeral=True)
        return
    except Exception:
        log.exception("Unexpected BIP-322 verifier error")
        await interaction.followup.send(
            "❌ Verifier crashed — check bot logs.", ephemeral=True
        )
        return

    # Consume the nonce immediately — single-use even if the steps below fail.
    _pending.pop(discord_id, None)
    record_verification(discord_id, chal.btc_address)

    try:
        inscriptions = await fetch_inscriptions(chal.btc_address)
    except Exception:
        log.exception("Ordiscan inscription lookup failed for %s", chal.btc_address)
        inscriptions = []

    granted_lines: list[str] = []
    failed_lines: list[str] = []

    # Base "verified" role (if configured)
    if VERIFIED_ROLE_ID and isinstance(interaction.user, discord.Member):
        role = interaction.guild.get_role(VERIFIED_ROLE_ID) if interaction.guild else None
        if role:
            try:
                await interaction.user.add_roles(role, reason="Ordinal ownership verified")
                granted_lines.append(f"<@&{VERIFIED_ROLE_ID}> (base verified role)")
            except discord.Forbidden:
                failed_lines.append(
                    f"<@&{VERIFIED_ROLE_ID}> — bot lacks Manage Roles or sits below this role"
                )

    # Per-collection holder rules
    if interaction.guild and isinstance(interaction.user, discord.Member):
        rule_role_ids = evaluate_rules(str(interaction.guild.id), inscriptions)
        for rid in rule_role_ids:
            role = interaction.guild.get_role(int(rid))
            if not role:
                failed_lines.append(f"<@&{rid}> — role no longer exists")
                continue
            if role in interaction.user.roles:
                granted_lines.append(f"<@&{rid}> (already had it)")
                continue
            try:
                await interaction.user.add_roles(role, reason="Holder-rule match")
                granted_lines.append(f"<@&{rid}>")
            except discord.Forbidden:
                failed_lines.append(
                    f"<@&{rid}> — bot lacks Manage Roles or sits below this role"
                )

    embed = discord.Embed(
        title="✅ Ownership verified",
        description=f"Address `{chal.btc_address}` linked to <@{discord_id}>",
        color=discord.Color.green(),
    )
    embed.add_field(name="Inscriptions held", value=str(len(inscriptions)), inline=True)
    if inscriptions:
        sample = "\n".join(
            f"• #{i.get('number', '?')} `{str(i.get('id', '?'))[:16]}…`"
            for i in inscriptions[:5]
        )
        embed.add_field(name="First 5", value=sample, inline=False)
    if granted_lines:
        embed.add_field(name="Roles granted", value="\n".join(granted_lines), inline=False)
    if failed_lines:
        embed.add_field(name="⚠️ Could not grant", value="\n".join(failed_lines), inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="whoami", description="Show your verified Bitcoin addresses.")
async def whoami_cmd(interaction: discord.Interaction):
    rows = list_verifications(str(interaction.user.id))
    if not rows:
        await interaction.response.send_message("No addresses verified yet.", ephemeral=True)
        return
    body = "\n".join(f"`{addr}` — verified <t:{ts}:R>" for addr, ts in rows)
    await interaction.response.send_message(body, ephemeral=True)


# ----- admin: holder-rule management -------------------------------------

_RULE_TYPE_CHOICES = [
    app_commands.Choice(name="inscription (single specific NFT)", value="inscription"),
    app_commands.Choice(name="parent (every child of this parent inscription)", value="parent"),
    app_commands.Choice(name="collection (Ordiscan-indexed collection slug)", value="collection"),
]


@bot.tree.command(
    name="rule_help",
    description="How holder-rules work and how to find the value for /rule_add.",
)
@app_commands.default_permissions(administrator=True)
async def rule_help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Holder-rule setup guide",
        color=discord.Color.blurple(),
        description=(
            "Tapseal can grant roles to anyone who proves ownership of a specific "
            "ordinal. Rules are evaluated automatically inside `/submit` after a "
            "successful BIP-322 verification."
        ),
    )
    embed.add_field(
        name="1. Create the role in Discord",
        value=(
            "Server Settings → Roles → Create Role → name it (e.g. `pizzaowner`)."
        ),
        inline=False,
    )
    embed.add_field(
        name="2. Position Tapseal above it",
        value=(
            "Drag Tapseal's role **above** the role you just made in the role "
            "list. Discord only lets a role grant roles strictly below itself."
        ),
        inline=False,
    )
    embed.add_field(
        name="3. Pick a match type",
        value=(
            "**inscription** — one specific NFT only.\n"
            "**parent** — every child of a parent inscription. Best for on-chain "
            "recursive collections (Pizza Pepes, Bitcoin Frogs, etc).\n"
            "**collection** — an Ordiscan-indexed collection slug (most curated "
            "collections; some don't have one)."
        ),
        inline=False,
    )
    embed.add_field(
        name="4. Find the value",
        value=(
            "**inscription** / **parent**: the 64-hex+`i`+index ID, e.g. "
            "`abc…123i0`. Visit the inscription on <https://ordiscan.com> — the URL "
            "ends in the ID. If the page shows a **Parent** field, that's the "
            "parent inscription's ID — use it with `type:parent` to cover the "
            "whole collection.\n"
            "**collection**: the URL slug, e.g. `bitcoin-frogs` from "
            "`https://ordiscan.com/collection/bitcoin-frogs`."
        ),
        inline=False,
    )
    embed.add_field(
        name="5. Add the rule",
        value=(
            "`/rule_add type:<...> value:<id-or-slug> role:@yourrole`\n"
            "The bot will look up the value live and tell you if it's a child of "
            "another inscription (in which case `type:parent` is usually better)."
        ),
        inline=False,
    )
    embed.add_field(
        name="Manage existing rules",
        value="`/rule_list`  •  `/rule_remove rule_id:<n>`",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="rule_add",
    description="Bind a Discord role to ownership of an inscription, parent, or collection.",
)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    type="What to match against (see /rule_help)",
    value="Inscription ID (e.g. abc…i0), parent inscription ID, or collection slug",
    role="Role to grant when a verified user holds a matching inscription",
)
@app_commands.choices(type=_RULE_TYPE_CHOICES)
async def rule_add_cmd(
    interaction: discord.Interaction,
    type: app_commands.Choice[str],
    value: str,
    role: discord.Role,
):
    if not interaction.guild:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    # Hierarchy sanity check — even if we save the rule, granting will fail later
    bot_member = interaction.guild.me
    hierarchy_warn = ""
    if bot_member and role >= bot_member.top_role:
        hierarchy_warn = (
            f"\n⚠️ Tapseal's top role is **{bot_member.top_role.name}** which is "
            f"not above **{role.name}** — drag Tapseal's role higher in "
            "Server Settings → Roles, otherwise grants will fail with 403."
        )

    preview = ""
    suggestion = ""
    mtype = type.value
    mvalue = value.strip()

    # Live validation against Ordiscan
    try:
        if mtype in ("inscription", "parent"):
            insc = await fetch_inscription(mvalue)
            if insc is None:
                await interaction.followup.send(
                    f"❌ Ordiscan doesn't recognize `{mvalue}` as an inscription ID. "
                    "Double-check the ID — should be 64 hex chars + `i` + index.",
                    ephemeral=True,
                )
                return
            preview = (
                f"Inscription #{insc.get('inscription_number')} • "
                f"`{insc.get('content_type')}`"
            )
            if mtype == "inscription" and insc.get("parent_inscription_id"):
                suggestion = (
                    f"\n💡 This inscription is a **child** of "
                    f"`{insc['parent_inscription_id']}`. To cover the whole "
                    f"collection, you probably want:\n"
                    f"`/rule_add type:parent value:{insc['parent_inscription_id']} role:@{role.name}`"
                )
            if mtype == "parent":
                child_count = await count_children(mvalue)
                if child_count == 0:
                    await interaction.followup.send(
                        f"❌ `{mvalue}` is a valid inscription but has no children "
                        "on-chain. Either it's not a parent, or its children "
                        "haven't been inscribed yet. Use `type:inscription` if "
                        "you only want to match this specific NFT.",
                        ephemeral=True,
                    )
                    return
                more = "+" if child_count >= 100 else ""
                preview += f" • {child_count}{more} children"
    except Exception:
        log.exception("Ordiscan validation failed during /rule_add")
        # Don't block on transient failures; admin can still save the rule.
        preview = "(could not validate — Ordiscan unreachable, saving anyway)"

    rule_id = add_role_rule(
        guild_id=str(interaction.guild.id),
        match_type=mtype,
        match_value=mvalue,
        role_id=str(role.id),
    )

    body = (
        f"✅ Rule **#{rule_id}** saved.\n"
        f"**Match:** `{mtype}` → `{mvalue}`\n"
        f"**Role:** {role.mention}\n"
        f"**Preview:** {preview}"
        f"{suggestion}{hierarchy_warn}"
    )
    await interaction.followup.send(body, ephemeral=True)


@bot.tree.command(
    name="rule_remove",
    description="Delete a holder-rule by its rule_id (see /rule_list).",
)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(rule_id="The rule's numeric ID — copy from /rule_list")
async def rule_remove_cmd(interaction: discord.Interaction, rule_id: int):
    if not interaction.guild:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    ok = remove_role_rule(rule_id, str(interaction.guild.id))
    if ok:
        await interaction.response.send_message(
            f"✅ Removed rule **#{rule_id}**.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ No rule with ID **{rule_id}** in this server.", ephemeral=True
        )


@bot.tree.command(
    name="rule_list",
    description="Show all holder-rules configured for this server.",
)
@app_commands.default_permissions(administrator=True)
async def rule_list_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Run this in a server.", ephemeral=True)
        return
    rules = list_role_rules(str(interaction.guild.id))
    if not rules:
        await interaction.response.send_message(
            "No holder-rules configured. Run `/rule_help` to learn how to add one.",
            ephemeral=True,
        )
        return
    lines = []
    for rid, mtype, mvalue, role_id in rules:
        short = mvalue if len(mvalue) <= 24 else mvalue[:20] + "…" + mvalue[-4:]
        lines.append(f"`#{rid}` • `{mtype}` → `{short}` → <@&{role_id}>")
    embed = discord.Embed(
        title=f"Holder-rules ({len(rules)})",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ----- error handling ----------------------------------------------------

async def _on_app_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    log.exception("Slash command error: %s", error)
    msg = f"Error: `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


verify_cmd.error(_on_app_error)
submit_cmd.error(_on_app_error)
whoami_cmd.error(_on_app_error)
rule_help_cmd.error(_on_app_error)
rule_add_cmd.error(_on_app_error)
rule_remove_cmd.error(_on_app_error)
rule_list_cmd.error(_on_app_error)


def main() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN not set — copy .env.example to .env and fill it in.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
