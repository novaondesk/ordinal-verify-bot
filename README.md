# Ordinal Verify Bot

Discord bot that proves a user controls a Bitcoin address — and lists any
Ordinal inscriptions held there — using **BIP-322** signed messages.

## How it works

1. User runs `/verify address:<btc_addr>`. The bot returns a single-use,
   5-minute challenge string bound to the user's Discord ID, the supplied
   address, and a fresh random nonce.
2. User signs the challenge in their wallet (Sparrow / Leather / Xverse /
   Unisat / any BIP-322-capable wallet).
3. User runs `/submit signature:<base64>`. The bot:
   - Verifies the signature with the Rust-backed `bip322` library
     (`verify_simple_encoded`).
   - Records the `discord_id ↔ btc_address` binding in SQLite.
   - Looks up inscriptions held by the address via the Hiro Ordinals API.
   - Optionally grants a configured role.

All bot replies are **ephemeral**, so addresses and signatures are never
visible to other channel members.

## Commands

| Command                       | What it does                                          |
| ----------------------------- | ----------------------------------------------------- |
| `/verify address:<btc_addr>`  | Issue a fresh signing challenge.                      |
| `/submit signature:<base64>`  | Submit the BIP-322 signature for the active challenge.|
| `/whoami`                     | List the addresses you've verified.                   |

## Setup

```bash
git clone https://github.com/novaoc/ordinal-verify-bot.git
cd ordinal-verify-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit .env
python ordinal_verify_bot.py
```

### Discord application

1. Create an application + bot at <https://discord.com/developers/applications>.
2. Copy the bot token into `DISCORD_TOKEN` in `.env`.
3. Invite the bot with the `applications.commands` scope (and `bot` scope with
   `Manage Roles` only if you want `VERIFIED_ROLE_ID` to work).

### Optional config

- `GUILD_ID` — register slash commands to one guild for instant updates. Leave
  unset for global commands (propagation can take up to ~1 hour).
- `VERIFIED_ROLE_ID` — role granted on successful verification. The bot's top
  role must sit above this role.
- `HIRO_API_KEY` — raises rate limits on the inscription lookup.

## Security notes

- The challenge message binds `discord_id`, `btc_address`, and a 128-bit
  random nonce, so signatures cannot be replayed across users or addresses.
- Nonces are single-use and expire after 5 minutes; pending challenges are
  held in memory only and cleared on bot restart.
- Slash command responses use `ephemeral=True` so signatures aren't broadcast.
- The bot uses `discord.Intents.default()` only — no privileged intents.

## License

MIT
