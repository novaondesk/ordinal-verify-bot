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
   - Looks up inscriptions held by the address via the **Ordiscan API**.
   - Optionally grants a configured role or applies holder-rules based on
     inscription ownership.
   - Shows a link to a mobile-friendly signing page (Sats Connect) if
     configured.

All bot replies are **ephemeral**, so addresses and signatures are never
visible to other channel members.

## New Features (PR #5)

- **Migrated to Ordiscan API**: Replaced deprecated Hiro API with Ordiscan's
  free-tier-friendly API (no rate limits for basic usage).
- **Holder-Rule System**: Grant Discord roles automatically based on which
  inscriptions a user holds. Rules can match:
  - Specific inscription ID
  - Parent inscription (covers entire collection)
  - Collection slug (e.g., "bitcoin-frogs")
- **New Slash Commands**:
  - `/rule_add type:<inscription|parent|collection> value:<id|slug> role:@role`
  - `/rule_remove rule_id:<n>`
  - `/rule_list`
  - `/rule_help`
- **Mobile-Friendly Signing**: When `SIGN_PAGE_URL` is set, `/verify` shows a
  button linking to a standalone signing page (Sats Connect) that opens the
  user's wallet app directly.
- **Privacy Policy & Terms of Service**: Added `PRIVACY.md` and `TERMS.md`
  for transparency and compliance.
- **Comprehensive Test Suite**: Added unit tests for bot logic, inscription
  fetching, and role-rule evaluation.

## Commands

| Command                       | What it does                                                                 |
|-------------------------------|------------------------------------------------------------------------------|
| `/verify address:<btc_addr>`  | Issue a fresh signing challenge.                                             |
| `/submit signature:<base64>`  | Submit the BIP-322 signature for the active challenge.                       |
| `/whoami`                     | List the addresses you've verified.                                          |
| `/rule_add`                   | Bind a Discord role to ownership of an inscription, parent, or collection.   |
| `/rule_remove`                | Delete a holder-rule by its rule_id.                                         |
| `/rule_list`                  | Show all holder-rules configured for this server.                            |
| `/rule_help`                  | Guide to setting up holder-rules.                                            |

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

### Configuration

- `DISCORD_TOKEN` — Bot token from Discord developer portal.
- `GUILD_ID` — Register slash commands to one guild for instant updates. Leave
  unset for global commands (propagation can take up to ~1 hour).
- `VERIFIED_ROLE_ID` — Role granted on successful verification. The bot's top
  role must sit above this role.
- `ORDISCAN_API` — Ordiscan API endpoint (default: `https://api.ordiscan.com`).
- `ORDISCAN_API_KEY` — API key (required — without one the bot still verifies
  ownership but always reports 0 inscriptions). Get a free key at
  <https://ordiscan.com/docs/api/login>.
- `DB_PATH` — SQLite database path (default: `verifications.db`).
- `SIGN_PAGE_URL` — Optional URL of the static "Sign with wallet" page. When set,
  `/verify` shows a link button that opens this page with the challenge
  pre-filled, so mobile users don't have to copy the message into their wallet
  manually. Leave blank to omit the button.

### Security notes

- The challenge message binds `discord_id`, `btc_address`, and a 128-bit random
  nonce, so signatures cannot be replayed across users or addresses.
- Nonces are single-use and expire after 5 minutes; pending challenges are held
  in memory only and cleared on bot restart.
- Slash command responses use `ephemeral=True` so signatures aren't broadcast.
- The bot uses `discord.Intents.default()` only — no privileged intents.

## License

MIT

## Documentation

- Privacy Policy: `PRIVACY.md`
- Terms of Service: `TERMS.md`