# Privacy Policy — Tapseal

_Last updated: 2026-05-05_

## What we collect

When you successfully verify with `/verify` and `/submit`, Tapseal stores
**only**:

- Your Discord user ID (numeric, e.g. `123456789012345678`)
- The Bitcoin address you verified (e.g. `bc1q…`)
- A unix timestamp of when verification completed

That's it. We do **not** store:

- Your Discord username, display name, email, avatar, or any profile
  attribute
- Your BIP-322 signature (held only in memory during verification, then
  discarded)
- The nonce/challenge (in memory only, deleted on success or expiry)
- Your private keys, wallet seed, or transaction history (the bot never
  has access to these)
- Message content, channel names, voice activity, or anything outside the
  bot's slash commands

## Where it's stored

Records live in a local SQLite file (`verifications.db`) on the machine
where the operator runs the bot. The file does not leave that machine and
is not shared with third parties.

## Third parties

When you successfully verify, the bot calls **Xverse's Ordinals API** with
your Bitcoin address to count your inscriptions. Xverse may log this request
per their privacy policy: <https://www.xverse.app/>.

The bot necessarily communicates with **Discord** (gateway connection +
slash command responses). Discord's privacy policy applies to that traffic:
<https://discord.com/privacy>.

No other third parties receive your data.

## Retention

Records are kept indefinitely by default. The operator can delete the
SQLite file at any time, which removes all records.

## Your rights

You can:

- Ask the operator (in DM) what's stored about you — it's just the three
  fields above
- Ask the operator to delete your record
- Stop using the bot at any time; no further data is collected after that

## Changes

If this policy changes, the new version will be committed to the public
repository at <https://github.com/novaoc/ordinal-verify-bot>. Continued use
after a change constitutes acceptance.

## Contact

Direct questions to the server administrator who deployed this instance.
