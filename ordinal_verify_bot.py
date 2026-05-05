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
HIRO_API = os.getenv("HIRO_API", "https://api.hiro.so").rstrip("/")
HIRO_API_KEY = os.getenv("HIRO_API_KEY") or None
GUILD_ID = int(os.getenv("GUILD_ID", "0")) or None
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0")) or None
DB_PATH = os.getenv("DB_PATH", "verifications.db")
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


# ----- ordinals lookup ---------------------------------------------------

async def fetch_inscriptions(address: str, max_pages: int = 10) -> list[dict]:
    """Hiro Ordinals API. Public; HIRO_API_KEY raises rate limits."""
    url = f"{HIRO_API}/ordinals/v1/inscriptions"
    headers = {"Accept": "application/json"}
    if HIRO_API_KEY:
        headers["x-api-key"] = HIRO_API_KEY
    out: list[dict] = []
    offset = 0
    limit = 60
    async with httpx.AsyncClient(timeout=15) as client:
        for _ in range(max_pages):
            r = await client.get(
                url,
                params={"address": address, "limit": limit, "offset": offset},
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("results", []) or []
            out.extend(results)
            total = data.get("total", len(out))
            offset += len(results)
            if not results or offset >= total:
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

    embed = discord.Embed(
        title="Sign this message with your Bitcoin wallet",
        description=(
            "**1.** Open your wallet (Sparrow, Leather, Xverse, Unisat, …) and use "
            "**Sign Message** for the address below.\n"
            "**2.** Sign **exactly** the message in the code block.\n"
            "**3.** Run `/submit signature:<base64>` within 5 minutes.\n\n"
            f"```\n{msg}\n```"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(name="Address", value=f"`{address}`", inline=False)
    embed.add_field(
        name="Expires", value=f"<t:{int(time.time()) + CHALLENGE_TTL_SEC}:R>", inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
        log.exception("Hiro inscription lookup failed for %s", chal.btc_address)
        inscriptions = []

    role_note = ""
    if VERIFIED_ROLE_ID and isinstance(interaction.user, discord.Member):
        role = interaction.guild.get_role(VERIFIED_ROLE_ID) if interaction.guild else None
        if role:
            try:
                await interaction.user.add_roles(role, reason="Ordinal ownership verified")
                role_note = f"\nRole granted: <@&{VERIFIED_ROLE_ID}>"
            except discord.Forbidden:
                role_note = "\n(Could not assign role — bot needs Manage Roles + role placed below it.)"

    embed = discord.Embed(
        title="✅ Ownership verified",
        description=f"Address `{chal.btc_address}` linked to <@{discord_id}>{role_note}",
        color=discord.Color.green(),
    )
    embed.add_field(name="Inscriptions held", value=str(len(inscriptions)), inline=True)
    if inscriptions:
        sample = "\n".join(
            f"• #{i.get('number', '?')} `{str(i.get('id', '?'))[:16]}…`"
            for i in inscriptions[:5]
        )
        embed.add_field(name="First 5", value=sample, inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="whoami", description="Show your verified Bitcoin addresses.")
async def whoami_cmd(interaction: discord.Interaction):
    rows = list_verifications(str(interaction.user.id))
    if not rows:
        await interaction.response.send_message("No addresses verified yet.", ephemeral=True)
        return
    body = "\n".join(f"`{addr}` — verified <t:{ts}:R>" for addr, ts in rows)
    await interaction.response.send_message(body, ephemeral=True)


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


def main() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN not set — copy .env.example to .env and fill it in.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
